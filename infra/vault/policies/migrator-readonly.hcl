# Prisma Migrate Vault Agent — read-only access to migration credentials.
# KV v2 requires /data/ in ACL paths.

path "kv/data/ft-transcendence/dev/migrator" {
  capabilities = ["read"]
}
