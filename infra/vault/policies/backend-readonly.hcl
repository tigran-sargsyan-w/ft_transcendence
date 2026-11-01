# Backend Vault Agent — read-only access to backend credentials.
# KV v2 requires /data/ in ACL paths.

path "kv/data/ft-transcendence/dev/backend" {
  capabilities = ["read"]
}
