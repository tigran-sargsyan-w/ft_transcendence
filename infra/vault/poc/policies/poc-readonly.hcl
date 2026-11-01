
# Vault POC — read-only access to one test secret
#
# KV v2 uses /data/ in its API and ACL paths.

path "kv/data/ft-transcendence/dev/poc-demo" {
  capabilities = ["read"]
}
