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

ensure_role() {
  role="$1"
  role_policy="$2"

  echo "Checking AppRole: $role"
  if role_state="$(vault read "auth/approle/role/$role" 2>&1)"; then
    assert_field token_policies "[$role_policy]"
    assert_field bind_secret_id true
    assert_field token_no_default_policy true
    assert_field secret_id_ttl 168h
    assert_field secret_id_num_uses 20
    assert_field token_ttl 1h
    assert_field token_max_ttl 4h
    echo "PASS: AppRole $role"
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
    secret_id_ttl=168h \
    secret_id_num_uses=20 \
    token_ttl=1h \
    token_max_ttl=4h >/dev/null
  echo "CREATED: AppRole $role"
  role_state="$(vault read "auth/approle/role/$role")" ||
    fail "Cannot verify newly created AppRole: $role"
  assert_field token_policies "[$role_policy]"
  assert_field bind_secret_id true
  assert_field token_no_default_policy true
  assert_field secret_id_ttl 168h
  assert_field secret_id_num_uses 20
  assert_field token_ttl 1h
  assert_field token_max_ttl 4h
}

ensure_role backend-reader backend-readonly
ensure_role migrator-reader migrator-readonly
ensure_role postgres-provisioner-reader postgres-provisioner-readonly

echo "RESULT: VAULT CONFIG READY ($MODE)"
