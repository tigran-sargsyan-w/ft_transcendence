#!/usr/bin/env bash
set -euo pipefail

umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TLS_DIR="${FT_DEV_TLS_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/transcendence/dev/tls}"
CA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/ft-transcendence/vault-ca"

CA_KEY="$CA_DIR/ca.key"
CA_CERT="$TLS_DIR/ca.crt"

command -v openssl >/dev/null ||
  { echo "Error: OpenSSL is required" >&2; exit 1; }

[[ -f "$CA_KEY" ]] ||
  { echo "Error: CA private key not found" >&2; exit 1; }

[[ -f "$CA_CERT" ]] ||
  { echo "Error: CA certificate not found" >&2; exit 1; }

# The CA must remain valid for the new certificate's lifetime.
openssl x509 -in "$CA_CERT" \
  -checkend "$((90 * 24 * 60 * 60))" -noout >/dev/null ||
  { echo "Error: CA expires within 90 days" >&2; exit 1; }

# Create a private staging directory.
WORK_DIR="$(mktemp -d "$TLS_DIR/.renew.XXXXXXXX")"

completed=false
cleanup() {
  if [[ "$completed" != true ]]; then
    rm -rf "$WORK_DIR"
  fi
}
trap cleanup EXIT

# Verify that the CA certificate matches its private key.
openssl x509 -in "$CA_CERT" -pubkey -noout |
  openssl pkey -pubin -outform DER > "$WORK_DIR/ca-cert.pub"

openssl pkey -in "$CA_KEY" -pubout -outform DER \
  > "$WORK_DIR/ca-key.pub"

cmp -s "$WORK_DIR/ca-cert.pub" "$WORK_DIR/ca-key.pub" ||
  { echo "Error: CA certificate/key mismatch" >&2; exit 1; }

echo "Generating new Vault server key and CSR..."

openssl req -new \
  -newkey rsa:3072 \
  -nodes \
  -sha256 \
  -keyout "$WORK_DIR/vault.key" \
  -out "$WORK_DIR/vault.csr" \
  -subj "/CN=vault"

cat > "$WORK_DIR/vault.ext" <<'EOF'
subjectAltName = DNS:vault
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
EOF

echo "Signing new Vault server certificate..."

openssl x509 -req \
  -in "$WORK_DIR/vault.csr" \
  -CA "$CA_CERT" \
  -CAkey "$CA_KEY" \
  -set_serial "0x$(openssl rand -hex 16)" \
  -out "$WORK_DIR/vault.crt" \
  -days 90 \
  -sha256 \
  -extfile "$WORK_DIR/vault.ext"

echo "Verifying new certificate..."

openssl verify \
  -CAfile "$CA_CERT" \
  "$WORK_DIR/vault.crt"

chmod 0600 "$WORK_DIR/vault.key"
chmod 0644 "$WORK_DIR/vault.crt"

rm -f \
  "$WORK_DIR/vault.csr" \
  "$WORK_DIR/vault.ext" \
  "$WORK_DIR/ca-cert.pub" \
  "$WORK_DIR/ca-key.pub"

completed=true

echo
echo "TLS renewal candidate generated successfully."
echo "Staging directory: $WORK_DIR"
echo "Existing Vault TLS files were not modified."
echo "Activation must be performed separately."
