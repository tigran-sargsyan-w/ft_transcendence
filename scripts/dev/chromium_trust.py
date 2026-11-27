"""Select the shared NSS database for native Linux Chromium-family browsers.

The caller must authorize the public CA before invoking these operations.
A trusted NSS entry is not proof that a particular browser has loaded it.
"""
import os
from pathlib import Path
import platform
import shutil

from scripts.dev import nss_trust as nss

BROWSERS = {
    "Chrome": ("google-chrome", "google-chrome-stable"),
    "Edge": ("microsoft-edge", "microsoft-edge-stable"),
    "Opera": ("opera",),
}


def installed_browsers():
    """Report discoverable executables, not TLS compatibility or readiness."""
    return tuple(name for name, commands in BROWSERS.items()
                 if any(shutil.which(command) for command in commands))


def selected_database():
    """Read-only selection following Chromium M146+ directory precedence.

    Existing legacy directories (including empty ones) take precedence.
    Never create a legacy directory just to support an older browser.
    Unknown or unsafe databases must be reviewed, not bypassed.
    """
    if platform.system() != "Linux" or "microsoft" in platform.release().lower():
        raise nss.NSSError("Shared NSS adapter requires native Linux")
    home = Path.home()
    legacy = home / ".pki/nssdb"
    # Validate existing ancestors even when the database itself is absent.
    nss.database_state(legacy)
    if legacy.exists():
        return legacy
    data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
    modern = data / "pki/nssdb"
    # Enforce the NSS manager's home/ownership and no-symlink policy.
    nss.database_state(modern)
    return modern


def check(ca_file):
    """Inspect the selected database, without creating directories or trust."""
    return nss.check(selected_database(), ca_file)


def install(ca_file):
    """Prepare the selected store only; caller owns CA authorization."""
    path = selected_database()
    result = nss.install(path, ca_file)
    if selected_database() != path:
        raise nss.NSSError("Shared NSS location changed; review before retrying")
    if nss.check(path, ca_file) != "trusted":
        raise nss.NSSError("Shared NSS trust not verified")
    return result
