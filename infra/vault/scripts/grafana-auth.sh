#!/bin/sh
# Provision only the dedicated Grafana AppRole credentials.

set -eu
umask 077

AUTH=/vault/auth
ROLE=auth/approle/role/grafana-reader
PENDING="$AUTH/.provisioning-pending"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

inventory() {
    [ -d "$AUTH" ] && [ ! -L "$AUTH" ] || return 3
    [ "$(stat -c '%u:%g:%a' "$AUTH")" = 100:1000:700 ] || return 3

    if [ -e "$PENDING" ] || [ -L "$PENDING" ]; then
        return 3
    fi

    for name in role_id secret_id; do
        path="$AUTH/$name"
        [ ! -L "$path" ] || return 3
    done

    if [ ! -e "$AUTH/role_id" ] && [ ! -e "$AUTH/secret_id" ]; then
        [ -z "$(ls -A "$AUTH")" ] || return 3
        return 2
    fi

    for name in role_id secret_id; do
        path="$AUTH/$name"

        [ -f "$path" ] && [ -s "$path" ] && [ -r "$path" ] || return 3
        [ "$(stat -c '%u:%g:%a' "$path")" = 100:1000:400 ] || return 3
    done

    return 0
}

mode="${1:-check}"

case "$mode" in
    check|apply) ;;
    *) fail "Unsupported Grafana auth mode" ;;
esac

state=0
inventory || state=$?

case "$state" in
    0)
        echo "PRESERVED: Grafana AppRole credentials"
        exit 0
        ;;
    2)
        if [ "$mode" = check ]; then
            echo "MISSING: Grafana AppRole credentials"
            exit 2
        fi
        ;;
    *)
        fail "Partial, unsafe or interrupted Grafana credential provisioning"
        ;;
esac

# The trusted host driver provides the operator token over stdin.
IFS= read -r VAULT_TOKEN || fail "Missing operator authentication"

[ -n "$VAULT_TOKEN" ] || fail "Empty operator authentication"

export VAULT_TOKEN
trap 'unset VAULT_TOKEN' EXIT

vault token lookup >/dev/null 2>&1 ||
    fail "Vault operator authentication failed"

# Never overwrite an existing interruption marker.
# If provisioning fails after this point, retain the marker and stop.
(
    set -C
    : > "$PENDING"
) || fail "Cannot establish provisioning marker"

vault read -field=role_id "$ROLE/role-id" \
    > "$AUTH/.role_id.next" 2>/dev/null ||
    fail "Cannot obtain Grafana RoleID"

vault write -force -field=secret_id "$ROLE/secret-id" \
    > "$AUTH/.secret_id.next" 2>/dev/null ||
    fail "Cannot issue Grafana SecretID"

test -s "$AUTH/.role_id.next" ||
    fail "Missing Grafana RoleID"

test -s "$AUTH/.secret_id.next" ||
    fail "Missing Grafana SecretID"

chmod 0400 "$AUTH/.role_id.next" "$AUTH/.secret_id.next"

mv "$AUTH/.role_id.next" "$AUTH/role_id"
mv "$AUTH/.secret_id.next" "$AUTH/secret_id"

rm "$PENDING"

inventory || fail "Grafana credential verification failed"

echo "CREATED: Grafana AppRole credentials"
