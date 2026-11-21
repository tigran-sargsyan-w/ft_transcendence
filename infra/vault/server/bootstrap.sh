#!/bin/sh
# Idempotent Vault ACL/AppRole setup for local development.
# FT_BOOTSTRAP_MODE=check validates without updating Vault state.
set -eu
umask 077

MODE="${FT_BOOTSTRAP_MODE:-apply}"
case "$MODE" in
  apply|check) ;;
  *) echo 'ERROR: Unsupported Vault bootstrap mode' >&2; exit 2 ;;
esac

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

is_check() { [ "$MODE" = check ]; }

# Local development AppRoles. The only supported existing-role migration is
# from the exact prior 20-use / 168-hour policy, including partial retries.
SECRET_ID_USES=0
SECRET_ID_TTL=2160h
PREVIOUS_SECRET_ID_USES=20
PREVIOUS_SECRET_ID_TTL=168h

echo "Checking Vault authentication..."
vault token lookup >/dev/null 2>&1 ||
  fail "Vault authentication failed"

echo "Checking KV v2 mount..."
kv_mount="$(vault secrets list -detailed)" ||
  fail "Cannot inspect Vault secrets mounts"
kv_mount="$(printf '%s\n' "$kv_mount" | awk '$1 == "kv/" { print }')"

if [ -z "$kv_mount" ]; then
  is_check && fail "Missing required kv/ mount"
  vault secrets enable -path=kv -version=2 kv
  echo "CREATED: KV v2 mount"
else
  case "$kv_mount" in
    *'map[version:2]'*) echo "PASS: KV v2 mount" ;;
    *) fail "Existing kv/ mount is not KV v2" ;;
  esac
fi

echo "Checking AppRole auth method..."
auth_list="$(vault auth list)" || fail "Cannot inspect Vault auth mounts"
approle_type="$(printf '%s\n' "$auth_list" | awk '$1 == "approle/" { print $2 }')"
if [ -z "$approle_type" ]; then
  is_check && fail "Missing AppRole auth method"
  vault auth enable approle
  echo "CREATED: AppRole auth"
elif [ "$approle_type" = "approle" ]; then
  echo "PASS: AppRole auth"
else
  fail "Unexpected auth method at approle/"
fi

ensure_policy() {
  policy="$1"
  file="/vault/policies/$policy.hcl"
  [ -r "$file" ] || fail "Missing local policy: $policy"
  if current="$(vault policy read "$policy" 2>/dev/null)"; then
    expected="$(cat "$file")"
    [ "$current" = "$expected" ] ||
      fail "Policy drift detected: $policy (manual review required)"
    echo "PASS: Existing policy $policy matches source"
  else
    is_check && fail "Required policy missing or inaccessible: $policy"
    vault policy write "$policy" "$file" >/dev/null
    actual="$(vault policy read "$policy")" || fail "Cannot verify new policy: $policy"
    expected="$(cat "$file")"
    [ "$actual" = "$expected" ] || fail "New policy mismatch: $policy"
    echo "CREATED: Policy $policy"
  fi
}

ensure_policy backend-readonly
ensure_policy migrator-readonly
ensure_policy postgres-provisioner-readonly

read_field() {
  printf '%s\n' "$role_state" |
    awk -v field="$1" '$1 == field { print $2; exit }'
}

assert_field() {
  actual="$(read_field "$1")"
  [ "$actual" = "$2" ] ||
    fail "AppRole $role: unexpected $1 (manual review required)"
}

assert_role_invariants() {
  # Verify every security-critical setting before updating an existing role.
  # AppRole policies, SecretID binding and token TTL are NEVER migrated here.
  assert_field token_policies "[$role_policy]"
  assert_field bind_secret_id true
  assert_field token_no_default_policy true
  assert_field token_ttl 1h
  assert_field token_max_ttl 4h
}

assert_new_limits() {
  assert_field secret_id_ttl "$SECRET_ID_TTL"
  assert_field secret_id_num_uses "$SECRET_ID_USES"
}

ensure_role() {
  role="$1"
  role_policy="$2"

  echo "Checking AppRole: $role"
  if role_state="$(vault read "auth/approle/role/$role" 2>&1)"; then
    assert_role_invariants
    uses="$(read_field secret_id_num_uses)"
    ttl="$(read_field secret_id_ttl)"

    if [ "$uses" != "$SECRET_ID_USES" ] || [ "$ttl" != "$SECRET_ID_TTL" ]; then
      # A routine --check must never mutate a live Vault role.
      is_check && fail "AppRole $role: SecretID limits differ; explicit provisioning required"

      # Refuse arbitrary drift. A partial migration is allowed because each
      # Vault property write is separate and a previous apply may have failed.
      case "$uses" in
        "$PREVIOUS_SECRET_ID_USES"|"$SECRET_ID_USES") ;;
        *) fail "AppRole $role: unexpected secret_id_num_uses (manual review required)" ;;
      esac
      case "$ttl" in
        "$PREVIOUS_SECRET_ID_TTL"|"$SECRET_ID_TTL") ;;
        *) fail "AppRole $role: unexpected secret_id_ttl (manual review required)" ;;
      esac

      if [ "$uses" = "$PREVIOUS_SECRET_ID_USES" ]; then
        vault write "auth/approle/role/$role/secret-id-num-uses" \
          secret_id_num_uses="$SECRET_ID_USES" >/dev/null 2>&1 ||
          fail "AppRole $role: cannot update SecretID usage limit"
      fi
      if [ "$ttl" = "$PREVIOUS_SECRET_ID_TTL" ]; then
        vault write "auth/approle/role/$role/secret-id-ttl" \
          secret_id_ttl="$SECRET_ID_TTL" >/dev/null 2>&1 ||
          fail "AppRole $role: cannot update SecretID lifetime"
      fi
      role_state="$(vault read "auth/approle/role/$role")" ||
        fail "Cannot verify migrated AppRole: $role"
      assert_role_invariants
      assert_new_limits
      echo "UPDATED: AppRole $role SecretID limits (existing SecretIDs not rotated)"
    else
      assert_new_limits
      echo "PASS: AppRole $role"
    fi
    return
  fi

  case "$role_state" in
    *"No value found at"*)
      is_check && fail "Missing AppRole: $role"
      ;;
    *) fail "Cannot inspect AppRole: $role" ;;
  esac

  vault write "auth/approle/role/$role" \
    token_policies="$role_policy" \
    token_no_default_policy=true \
    bind_secret_id=true \
    secret_id_ttl="$SECRET_ID_TTL" \
    secret_id_num_uses="$SECRET_ID_USES" \
    token_ttl=1h \
    token_max_ttl=4h >/dev/null ||
    fail "Cannot create AppRole: $role"
  echo "CREATED: AppRole $role"
  role_state="$(vault read "auth/approle/role/$role")" ||
    fail "Cannot verify newly created AppRole: $role"
  assert_role_invariants
  assert_new_limits
}

ensure_approle_mount_limit() {
  # By default Vault caps AppRole SecretIDs at the auth mount's max TTL
  # (normally 768h), even when a role requests 2160h. Only an explicitly
  # authorized apply may extend this DEV mount's cap to 90 days.
  tune_state="$(vault read sys/auth/approle/tune)" ||
    fail "Cannot inspect AppRole auth mount TTL"
  mount_max="$(printf '%s\n' "$tune_state" |
    awk '$1 == "max_lease_ttl" { print $2; exit }')"
  if [ "$mount_max" != "$SECRET_ID_TTL" ]; then
    is_check && fail "AppRole auth mount max TTL differs; explicit provisioning required"
    [ "$mount_max" = 768h ] ||
      fail "Unexpected AppRole auth mount max TTL (manual review required)"
    vault auth tune -max-lease-ttl="$SECRET_ID_TTL" approle/ >/dev/null 2>&1 ||
      fail "Cannot update AppRole auth mount max TTL"
    tune_state="$(vault read sys/auth/approle/tune)" ||
      fail "Cannot verify AppRole auth mount max TTL"
    mount_max="$(printf '%s\n' "$tune_state" |
      awk '$1 == "max_lease_ttl" { print $2; exit }')"
    [ "$mount_max" = "$SECRET_ID_TTL" ] ||
      fail "AppRole auth mount TTL update not confirmed"
    echo "UPDATED: DEV AppRole auth mount max TTL ($SECRET_ID_TTL)"
  else
    echo "PASS: DEV AppRole auth mount max TTL"
  fi
}

ensure_role backend-reader backend-readonly
ensure_role migrator-reader migrator-readonly
ensure_role postgres-provisioner-reader postgres-provisioner-readonly

ensure_approle_mount_limit

echo "RESULT: VAULT CONFIG READY ($MODE)"
