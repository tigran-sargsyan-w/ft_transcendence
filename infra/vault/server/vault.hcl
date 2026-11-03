
# Vault Server — isolated local POC

ui = false

disable_mlock = true

storage "raft" {
  path    = "/vault/data"
  node_id = "vault-poc-1"
}

listener "tcp" {
  address         = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"

  tls_cert_file = "/vault/tls/vault.crt"
  tls_key_file  = "/vault/tls/vault.key"

  tls_min_version = "tls12"
}

api_addr     = "https://vault:8200"
cluster_addr = "https://vault:8201"
