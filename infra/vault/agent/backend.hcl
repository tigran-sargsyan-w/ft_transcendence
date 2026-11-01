# Backend Vault Agent — dedicated secret delivery

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
  contents = "{{ with secret \"kv/data/ft-transcendence/dev/backend\" }}{{ .Data.data.database_url }}{{ end }}"

  destination = "/vault/rendered/database_url"

  perms = "0440"
  backup = false
  error_on_missing_key = true
}