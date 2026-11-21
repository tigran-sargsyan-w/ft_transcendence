#!/bin/sh
# Start Grafana Vault Agent or verify its rendered secrets.

set -eu
umask 077

TOKEN=/vault/runtime/agent/token
RENDERED=/vault/rendered

case "${1:-}" in
    start)
        [ "$(stat -c '%u:%g:%a' "$RENDERED")" = 100:20030:750 ] ||
            exit 1

        mkdir -p /vault/runtime/agent
        chmod 0700 /vault/runtime/agent

        # Require fresh rendering after every Agent startup.
        rm -f "$TOKEN"
        rm -f "$RENDERED/admin_password" "$RENDERED/secret_key"

        exec vault agent -config=/vault/config/grafana.hcl
        ;;

    check)
        [ -f "$TOKEN" ] && [ ! -L "$TOKEN" ] &&
            [ -s "$TOKEN" ] && [ -r "$TOKEN" ] || exit 1

        [ "$(stat -c '%u:%a' "$TOKEN")" = 100:400 ] ||
            exit 1

        for field in admin_password secret_key; do
            path="$RENDERED/$field"

            [ -f "$path" ] && [ ! -L "$path" ] &&
                [ -s "$path" ] && [ -r "$path" ] || exit 1

            [ "$(stat -c '%u:%g:%a' "$path")" = 100:20030:440 ] ||
                exit 1

            [ "$(wc -c < "$path")" -eq 64 ] || exit 1

            grep -Eq '^[0-9a-f]{64}$' "$path" || exit 1

            # Tokens and secret values are never printed.
            expected="$(
                VAULT_TOKEN="$(cat "$TOKEN")" \
                    vault kv get -field="$field" \
                    -mount=kv ft-transcendence/dev/grafana \
                    2>/dev/null
            )" || exit 1

            actual="$(cat "$path")"

            [ "$actual" = "$expected" ] || exit 1

            unset actual expected
        done

        echo "PASS: Grafana Vault-rendered secrets verified"
        ;;

    *)
        echo "ERROR: Unsupported Grafana Agent operation" >&2
        exit 2
        ;;
esac
