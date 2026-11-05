# PostgreSQL Provisioner:
# read only the two database credential records.

path "kv/data/ft-transcendence/dev/backend" {
  capabilities = ["read"]
}

path "kv/data/ft-transcendence/dev/migrator" {
  capabilities = ["read"]
}
