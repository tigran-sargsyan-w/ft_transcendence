#!/bin/sh
set -eu
umask 077

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

echo "Checking Vault authentication..."
vault token lookup >/dev/null 2>&1 ||
  fail "Vault authentication failed"

echo "Checking KV v2 mount..."

kv_mount="$(vault secrets list -detailed |
  awk '$1 == "kv/" { print }')"

if [ -z "$kv_mount" ]; then
  vault secrets enable -path=kv -version=2 kv
else
  case "$kv_mount" in
    *'map[version:2]'*)
      echo "KV v2 already configured"
      ;;
    *)
      fail "Existing kv/ mount is not KV v2"
      ;;
  esac
fi

echo "Checking AppRole auth method..."

approle_type="$(vault auth list |
  awk '$1 == "approle/" { print $2 }')"

if [ -z "$approle_type" ]; then
  vault auth enable approle
elif [ "$approle_type" = "approle" ]; then
  echo "AppRole already enabled"
else
  fail "Unexpected auth method at approle/"
fi

echo "Applying ACL policies..."

vault policy write \
  backend-readonly \
  /vault/policies/backend-readonly.hcl

vault policy write \
  migrator-readonly \
  /vault/policies/migrator-readonly.hcl

vault policy write \
  postgres-provisioner-readonly \
  /vault/policies/postgres-provisioner-readonly.hcl

read_field() {
  printf '%s\n' "$role_state" |
    awk -v field="$1" '$1 == field { print $2; exit }'
}

assert_field() {
  actual="$(read_field "$1")"
  [ "$actual" = "$2" ] ||
    fail "AppRole $role: $1 is '$actual', expected '$2'"
}

ensure_role() {
  role="$1"
  policy="$2"

  echo "Checking AppRole: $role"

  if role_state="$(vault read "auth/approle/role/$role" 2>&1)"; then
    assert_field token_policies "[$policy]"
    assert_field bind_secret_id true
    assert_field token_no_default_policy true
    assert_field secret_id_ttl 168h
    assert_field secret_id_num_uses 20
    assert_field token_ttl 1h
    assert_field token_max_ttl 4h

    echo "AppRole $role already configured"
    return
  fi

  case "$role_state" in
    *"No value found at"*)
      echo "Creating AppRole: $role"
      ;;
    *)
      fail "Unable to inspect AppRole $role"
      ;;
  esac

  vault write "auth/approle/role/$role" \
    token_policies="$policy" \
    token_no_default_policy=true \
    bind_secret_id=true \
    secret_id_ttl=168h \
    secret_id_num_uses=20 \
    token_ttl=1h \
    token_max_ttl=4h

  echo "AppRole $role created"
}

ensure_role backend-reader backend-readonly
ensure_role migrator-reader migrator-readonly
ensure_role postgres-provisioner-reader postgres-provisioner-readonly

echo "Vault bootstrap completed successfully"
