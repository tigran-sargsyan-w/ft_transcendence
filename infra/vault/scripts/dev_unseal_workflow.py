"""Opt-in, development-only Vault Shamir unseal through GNOME Keyring.

WARNING: Keeping the threshold of shares in one host keyring weakens split
custody. Never use this as production auto-unseal. Manual Shamir unseal works
independently of this helper.
"""

import getpass
import json
import re
import shutil
import subprocess
from pathlib import Path

VAULT_CONTAINER = "transcendence-vault-dev-vault-1"
VAULT_PROJECT = "transcendence-vault-dev"
VAULT_NETWORK = "transcendence-vault-dev_vault_private"
VAULT_VOLUME = "transcendence-vault-dev_vault_data"
CLIENT_IMAGE = "transcendence-app-dev-backend:latest"
KEYRING_COLLECTION = "/org/freedesktop/secrets/collection/login"
KEYRING_APP = "ft-transcendence-vault-unseal-v1"

# Script only: no secrets in command arguments, container environment or logs.
NODE_CLIENT = r'''
const fs = require('node:fs');
const https = require('node:https');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { input += chunk; if(input.length > 4096) process.exit(3); });
process.stdin.on('end', () => {
  let command;
  try { command = JSON.parse(input); } catch (_) { process.exit(3); }
  if (!command || !['status','unseal'].includes(command.action) ||
      (command.action === 'unseal' && (typeof command.key !== 'string' || !command.key))) {
    process.exit(3);
  }
  const method = command.action === 'status' ? 'GET' : 'POST';
  const path = command.action === 'status' ? '/v1/sys/seal-status' : '/v1/sys/unseal';
  const body = method === 'POST' ? JSON.stringify({key: command.key}) : null;
  // Do not retain the original input after preparing the request.
  input = ''; command = null;
  const req = https.request({
    hostname: 'vault', port: 8200, servername: 'vault', method, path,
    ca: fs.readFileSync('/tmp/ca.crt'), rejectUnauthorized: true,
    timeout: 8000,
    headers: body === null ? {} : {'content-type':'application/json', 'content-length': Buffer.byteLength(body)},
  }, res => {
    let data = '';
    res.on('data', chunk => { data += chunk; if (data.length > 16384) process.exit(4); });
    res.on('end', () => {
      if (res.statusCode !== 200) process.exit(4);
      try {
        const s = JSON.parse(data);
        const allowed = ['initialized','sealed','type','storage_type','t','n','progress','cluster_id'];
        const safe = {};
        for (const key of allowed) if (Object.hasOwn(s,key)) safe[key] = s[key];
        process.stdout.write(JSON.stringify(safe));
      } catch (_) { process.exit(4); }
    });
  });
  req.on('timeout', () => req.destroy());
  req.on('error', () => process.exit(4));
  req.end(body === null ? undefined : body);
});
'''


class WorkflowError(Exception):
    """A deliberately non-secret error suitable for display."""


def _call(argv, *, data=None, timeout=20):
    try:
        return subprocess.run(
            argv, input=data, text=True, capture_output=True,
            check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkflowError("Required local command failed or timed out") from exc


def _json_docker(*argv):
    result = _call(["docker", *argv])
    if result.returncode:
        raise WorkflowError("Docker inspection failed")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("Unexpected Docker response") from exc


def _storage_identity():
    """Pin enrolment to an existing Vault data volume's creation timestamp."""
    containers = _json_docker("inspect", VAULT_CONTAINER)
    if len(containers) != 1:
        raise WorkflowError("Vault container is missing")
    container = containers[0]
    labels = container.get("Config", {}).get("Labels") or {}
    if labels.get("com.docker.compose.project") != VAULT_PROJECT:
        raise WorkflowError("Unexpected Vault Compose project")
    if not container.get("State", {}).get("Running"):
        raise WorkflowError("Vault is not running")
    mounts = container.get("Mounts", [])
    if not any(m.get("Type") == "volume" and m.get("Name") == VAULT_VOLUME
               and m.get("Destination") == "/vault/data" for m in mounts):
        raise WorkflowError("Vault is not using the expected Raft volume")
    volumes = _json_docker("volume", "inspect", VAULT_VOLUME)
    if len(volumes) != 1 or volumes[0].get("Name") != VAULT_VOLUME:
        raise WorkflowError("Vault volume was not found")
    created = volumes[0].get("CreatedAt")
    if not isinstance(created, str) or not created:
        raise WorkflowError("Vault volume creation identity is unavailable")
    return created


def _require_login_unlocked():
    """Do not create, unlock or alter the user's Keyring implicitly."""
    for name in ("secret-tool", "busctl", "docker"):
        if shutil.which(name) is None:
            raise WorkflowError(f"Required command not found: {name}")
    owner = _call(["busctl", "--user", "status", "org.freedesktop.secrets"])
    if owner.returncode or "gnome-keyring-daemon" not in owner.stdout:
        raise WorkflowError("GNOME Keyring Secret Service is not available")
    alias = _call([
        "busctl", "--user", "call", "org.freedesktop.secrets",
        "/org/freedesktop/secrets", "org.freedesktop.Secret.Service",
        "ReadAlias", "s", "default",
    ])
    if alias.returncode or alias.stdout.strip() != f'o "{KEYRING_COLLECTION}"':
        raise WorkflowError("Default Keyring is not the persistent login collection")
    status = _call([
        "busctl", "--user", "get-property", "org.freedesktop.secrets",
        KEYRING_COLLECTION, "org.freedesktop.Secret.Collection", "Locked",
    ])
    if status.returncode or status.stdout.strip() != "b false":
        raise WorkflowError("GNOME login Keyring must be unlocked first")


def _attributes(*, slot=None, created=None):
    attrs = ["application", KEYRING_APP, "vault-project", VAULT_PROJECT]
    if slot is not None:
        attrs += ["share-slot", str(slot)]
    if created is not None:
        attrs += ["volume-created-at", created]
    return attrs


def _lookup(slot, created):
    result = _call(["secret-tool", "lookup", *_attributes(slot=slot, created=created)])
    if result.returncode == 1:
        return None
    if result.returncode:
        raise WorkflowError("GNOME Keyring lookup failed")
    value = result.stdout.rstrip("\n")
    if not value:
        return None
    return value


def _store(slot, created, key):
    result = _call([
        "secret-tool", "store", f"--collection={KEYRING_COLLECTION}",
        f"--label=Transcendence dev Vault unseal share {slot}",
        *_attributes(slot=slot, created=created),
    ], data=key, timeout=30)
    if result.returncode:
        raise WorkflowError("GNOME Keyring store failed")


def _clear_all():
    result = _call(["secret-tool", "clear", *_attributes()])
    if result.returncode:
        raise WorkflowError("GNOME Keyring clear failed")


def _valid_share(value):
    return (
        isinstance(value, str)
        and 16 <= len(value) <= 1024
        and not any(ch.isspace() for ch in value)
        and not any(ord(ch) < 33 or ord(ch) > 126 for ch in value)
    )


def _vault_request(action, tls_dir, key=None):
    ca_file = tls_dir / "ca.crt"
    if not ca_file.is_file() or ca_file.is_symlink():
        raise WorkflowError("Expected Vault CA certificate is missing or a symlink")
    # Check existence and project topology; network need not publish host ports.
    network = _json_docker("network", "inspect", VAULT_NETWORK)
    if len(network) != 1 or network[0].get("Name") != VAULT_NETWORK:
        raise WorkflowError("Private Vault network unavailable")
    if (network[0].get("Labels") or {}).get("com.docker.compose.project") != VAULT_PROJECT:
        raise WorkflowError("Unexpected Vault network owner")
    if _call(["docker", "image", "inspect", CLIENT_IMAGE]).returncode:
        raise WorkflowError("Local HTTPS client image not available")
    command = [
        "docker", "run", "--rm", "-i", "--pull=never",
        "--network", VAULT_NETWORK,
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--user=65534:65534", "--pids-limit=64", "--memory=128m",
        "--mount", f"type=bind,src={ca_file.resolve()},dst=/tmp/ca.crt,readonly",
        "--entrypoint=node", CLIENT_IMAGE, "-e", NODE_CLIENT,
    ]
    data = {"action": action}
    if action == "unseal":
        data["key"] = key
    result = _call(command, data=json.dumps(data), timeout=35)
    if result.returncode:
        raise WorkflowError("Vault HTTPS request failed; verify TLS, network and Vault availability")
    try:
        response = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("Invalid Vault API response") from exc
    if not isinstance(response, dict) or not isinstance(response.get("sealed"), bool):
        raise WorkflowError("Vault status response missing sealed flag")
    return response


def _check_shamir(status, *, expect_initialized=True):
    if expect_initialized and status.get("initialized") is not True:
        raise WorkflowError("Vault is not initialized; refusing to initialize")
    if status.get("type") != "shamir" or status.get("t") != 2 or status.get("n") != 3:
        raise WorkflowError("Unexpected seal type or Shamir threshold; manual workflow required")


def enroll(tls_dir):
    _require_login_unlocked()
    created = _storage_identity()
    status = _vault_request("status", tls_dir)
    _check_shamir(status)
    if status["sealed"]:
        raise WorkflowError("Unseal manually before enrolling shares")
    if _lookup(1, created) or _lookup(2, created):
        raise WorkflowError("Shares already enrolled for this volume; use forget first")
    print("WARNING: DEV ONLY. Two threshold shares in one host Keyring weaken split-key custody.")
    print("INFO: Shares will not be displayed, logged or passed in process arguments.")
    if input("Type ENROLL to opt in: ").strip() != "ENROLL":
        raise WorkflowError("Enrollment cancelled")
    shares = []
    for slot in (1, 2):
        share = getpass.getpass(f"Shamir share {slot}: ")
        repeat = getpass.getpass(f"Confirm share {slot}: ")
        if share != repeat or not _valid_share(share):
            raise WorkflowError("A share was invalid or did not match its confirmation")
        if share in shares:
            raise WorkflowError("Shares must be distinct")
        shares.append(share)
    try:
        _store(1, created, shares[0])
        _store(2, created, shares[1])
    except WorkflowError:
        # A partial enrollment must not be usable.
        try:
            _clear_all()
        except WorkflowError:
            print("WARNING: Partial Keyring enrollment may remain; run forget")
        raise
    finally:
        shares.clear()
    print("PASS: Two development shares enrolled in persistent GNOME Keyring")
    print("INFO: Shares cannot be validated while Vault is already unsealed")
    return 0


def unseal(tls_dir):
    # Cheap HTTPS check first: never access Keyring for an already unsealed Vault.
    _storage_identity()
    state = _vault_request("status", tls_dir)
    _check_shamir(state)
    if not state["sealed"]:
        print("PASS: Vault already unsealed; no shares accessed")
        return 0
    if state.get("progress") != 0:
        raise WorkflowError("Partial unseal already in progress; use manual recovery")
    _require_login_unlocked()
    created = _storage_identity()
    shares = [_lookup(1, created), _lookup(2, created)]
    if any(not _valid_share(s) for s in shares) or shares[0] == shares[1]:
        raise WorkflowError("Two distinct enrolled shares were not found")
    for index, share in enumerate(shares, start=1):
        state = _vault_request("unseal", tls_dir, share)
        if index == 1 and (not state["sealed"] or state.get("progress") != 1):
            raise WorkflowError("Unexpected Vault unseal progress; stop and recover manually")
        if index == 2 and state["sealed"]:
            raise WorkflowError("Vault remains sealed; manual recovery required")
    shares.clear()
    status = _vault_request("status", tls_dir)
    if status["sealed"]:
        raise WorkflowError("Vault is still sealed after submitting threshold shares")
    print("PASS: Existing Vault unsealed through verified HTTPS")
    return 0


def forget():
    _require_login_unlocked()
    print("WARNING: This removes all host-enrolled shares for the development profile.")
    if input("Type FORGET to delete Keyring entries: ").strip() != "FORGET":
        raise WorkflowError("Deletion cancelled")
    _clear_all()
    print("PASS: Host-enrolled shares cleared; manual Vault unseal remains available")
    return 0


def run_command(command, tls_dir):
    try:
        if command == "enroll":
            return enroll(tls_dir)
        if command == "unseal":
            return unseal(tls_dir)
        if command == "forget":
            return forget()
        raise WorkflowError("Unknown command")
    except (WorkflowError, EOFError, KeyboardInterrupt):
        print("FAIL: Operation stopped safely; check Keyring, local Vault state and TLS")
        return 1
