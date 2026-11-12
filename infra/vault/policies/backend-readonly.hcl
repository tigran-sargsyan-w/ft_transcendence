# Backend Vault Agent — read-only access to backend credentials.
# KV v2 requires /data/ in ACL paths.

path "kv/data/ft-transcendence/dev/backend" {
  capabilities = ["read"]
}

# The AppRole deliberately excludes Vault's default policy.
# Grant only the self-service token operations required by Vault Agent.
path "auth/token/renew-self" {
  capabilities = ["update"]
}

path "auth/token/lookup-self" {
  capabilities = ["read"]
}
