
# Vault Agent — isolated POC

exit_after_auth = false

vault {
  address = "https://vault:8200"
  ca_cert = "/vault/tls/ca.crt"
}

auto_auth {
  method "approle" {
    mount_path = "auth/approle"

    config = {
      role_id_file_path = "/vault/auth/role_id"
      secret_id_file_path = "/vault/auth/secret_id"

      remove_secret_id_file_after_reading = false
    }
  }
}

template {
  contents = "{{ with secret \"kv/data/ft-transcendence/dev/poc-demo\" }}{{ .Data.data.demo_value }}{{ end }}"

  destination = "/vault/rendered/demo_value"

  perms = "0400"
  backup = false
  error_on_missing_key = true
}
