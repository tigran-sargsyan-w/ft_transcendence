#!/bin/sh
set -eu
umask 077

TLS_DIR="/vault/tls"
ACTION="${1:-}"
SOURCE_NAME="${2:-}"
BACKUP_NAME="${3:-}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

valid_name() {
  case "$1" in
    ""|*/*|*..*|*[!a-zA-Z0-9._-]*)
      return 1
      ;;
  esac
}

install_pair() {
  source_dir="$1"

  [ -s "$source_dir/vault.key" ] ||
    fail "Source private key missing"

  [ -s "$source_dir/vault.crt" ] ||
    fail "Source certificate missing"

  tmp_key="$(mktemp "$TLS_DIR/.vault.key.XXXXXXXX")"
  tmp_crt="$(mktemp "$TLS_DIR/.vault.crt.XXXXXXXX")"

  trap 'rm -f "$tmp_key" "$tmp_crt"' EXIT

  cp "$source_dir/vault.key" "$tmp_key"
  cp "$source_dir/vault.crt" "$tmp_crt"

  chown 100:1000 "$tmp_key"
  chmod 0400 "$tmp_key"
  chmod 0644 "$tmp_crt"

  mv -f "$tmp_key" "$TLS_DIR/vault.key"
  mv -f "$tmp_crt" "$TLS_DIR/vault.crt"

  trap - EXIT
}

case "$ACTION" in
  install)
    valid_name "$SOURCE_NAME" ||
      fail "Invalid candidate name"

    case "$SOURCE_NAME" in
      .renew.*) ;;
      *) fail "Expected a .renew.* directory" ;;
    esac

    valid_name "$BACKUP_NAME" ||
      fail "Invalid backup name"

    case "$BACKUP_NAME" in
      backup-*) ;;
      *) fail "Expected backup-* name" ;;
    esac

    candidate="$TLS_DIR/$SOURCE_NAME"
    backup="$TLS_DIR/.backups/$BACKUP_NAME"

    [ -d "$candidate" ] ||
      fail "Candidate directory not found"

    [ ! -L "$candidate" ] ||
      fail "Candidate directory must not be a symlink"

    [ -s "$TLS_DIR/vault.key" ] ||
      fail "Current private key missing"

    [ -s "$TLS_DIR/vault.crt" ] ||
      fail "Current certificate missing"

    mkdir -p "$TLS_DIR/.backups"
    chmod 0700 "$TLS_DIR/.backups"

    mkdir -m 0700 "$backup"

    cp "$TLS_DIR/vault.key" "$backup/vault.key"
    cp "$TLS_DIR/vault.crt" "$backup/vault.crt"

    chmod 0600 "$backup/vault.key" "$backup/vault.crt"

    install_pair "$candidate"

    echo "TLS candidate installed."
    echo "Backup: .backups/$BACKUP_NAME"
    ;;

  rollback)
    valid_name "$SOURCE_NAME" ||
      fail "Invalid backup name"

    case "$SOURCE_NAME" in
      backup-*) ;;
      *) fail "Expected backup-* name" ;;
    esac

    backup="$TLS_DIR/.backups/$SOURCE_NAME"

    [ -d "$backup" ] ||
      fail "Backup directory not found"

    install_pair "$backup"

    echo "Previous TLS certificate and key restored."
    ;;

  *)
    fail "Usage: tls-files.sh install CANDIDATE BACKUP | rollback BACKUP"
    ;;
esac
