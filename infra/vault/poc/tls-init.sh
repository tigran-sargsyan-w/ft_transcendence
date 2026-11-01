#!/usr/bin/env bash
set -euo pipefail

umask 077

cd "$(dirname "${BASH_SOURCE[0]}")"

TLS_DIR="$PWD/tls"
CA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/ft-transcendence/vault-poc-ca"

command -v openssl >/dev/null || {
    echo "Error: OpenSSL is required"
    exit 1
}

mkdir -p "$TLS_DIR" "$CA_DIR"
chmod 0755 "$TLS_DIR"
chmod 0700 "$CA_DIR"

# Never silently overwrite existing keys.
for file in \
    "$CA_DIR/ca.key" \
    "$TLS_DIR/ca.crt" \
    "$TLS_DIR/vault.key" \
    "$TLS_DIR/vault.crt"
do
    if [[ -e "$file" ]]; then
        echo "Error: $file already exists"
        exit 1
    fi
done

echo "Generating local Certificate Authority..."

openssl req -x509 \
    -newkey rsa:3072 \
    -nodes \
    -sha256 \
    -days 365 \
    -keyout "$CA_DIR/ca.key" \
    -out "$TLS_DIR/ca.crt" \
    -subj "/CN=Transcendence Vault POC CA" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"

echo "Generating Vault server key and CSR..."

openssl req -new \
    -newkey rsa:3072 \
    -nodes \
    -keyout "$TLS_DIR/vault.key" \
    -out "$TLS_DIR/vault.csr" \
    -subj "/CN=vault"

cat > "$TLS_DIR/vault.ext" <<'EOF'
subjectAltName = DNS:vault
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
EOF

echo "Signing Vault server certificate..."

openssl x509 -req \
    -in "$TLS_DIR/vault.csr" \
    -CA "$TLS_DIR/ca.crt" \
    -CAkey "$CA_DIR/ca.key" \
    -CAcreateserial \
    -out "$TLS_DIR/vault.crt" \
    -days 90 \
    -sha256 \
    -extfile "$TLS_DIR/vault.ext"

rm -f "$TLS_DIR/vault.csr" \
      "$TLS_DIR/vault.ext" \
      "$TLS_DIR/ca.srl"

chmod 0600 "$CA_DIR/ca.key" "$TLS_DIR/vault.key"
chmod 0644 "$TLS_DIR/ca.crt" "$TLS_DIR/vault.crt"

echo "Verifying certificate..."
openssl verify -CAfile "$TLS_DIR/ca.crt" "$TLS_DIR/vault.crt"

echo "TLS certificate generation completed."
