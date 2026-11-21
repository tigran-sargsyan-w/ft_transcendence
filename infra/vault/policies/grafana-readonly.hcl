# Grafana Vault Agent — read-only access to Grafana secrets.
# KV v2 requires /data/ in ACL paths.

path "kv/data/ft-transcendence/dev/grafana" {
  capabilities = ["read"]
}

# Only the self-service operations required by Vault Agent.
path "auth/token/renew-self" {
  capabilities = ["update"]
}

path "auth/token/lookup-self" {
  capabilities = ["read"]
}
