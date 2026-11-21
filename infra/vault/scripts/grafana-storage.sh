#!/bin/sh
# Initialize empty Grafana credential volumes without changing existing files.

set -eu
umask 077

fail() {
    echo "ERROR: Unsafe Grafana storage state" >&2
    exit 1
}

prepare_directory() {
    directory="$1"
    owner="$2"
    mode="$3"

    [ -d "$directory" ] && [ ! -L "$directory" ] || fail

    current="$(stat -c '%u:%g:%a' "$directory")"

    if [ "$current" = "$owner:$mode" ]; then
        echo "KEEP: $directory permissions"
        return
    fi

    # Never repair permissions on a populated credential volume
    # without explicit operator investigation.
    [ -z "$(ls -A "$directory")" ] || fail

    chown "$owner" "$directory"
    chmod "$mode" "$directory"

    [ "$(stat -c '%u:%g:%a' "$directory")" = "$owner:$mode" ] || fail

    echo "PREPARED: $directory permissions"
}

prepare_directory /vault/auth 100:1000 700
prepare_directory /vault/rendered 100:20030 750

echo "RESULT: GRAFANA CREDENTIAL STORAGE READY"
