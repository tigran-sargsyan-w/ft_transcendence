# Grafana Vault Agent — dedicated secret delivery.

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

  sink "file" {
    config = {
      path = "/vault/runtime/agent/token"
      mode = 0400
    }
  }
}

template {
  contents = "{{ with secret \"kv/data/ft-transcendence/dev/grafana\" }}{{ .Data.data.admin_password }}{{ end }}"

  destination = "/vault/rendered/admin_password"

  perms = "0440"
  backup = false
  error_on_missing_key = true
}

template {
  contents = "{{ with secret \"kv/data/ft-transcendence/dev/grafana\" }}{{ .Data.data.secret_key }}{{ end }}"

  destination = "/vault/rendered/secret_key"

  perms = "0440"
  backup = false
  error_on_missing_key = true
}
