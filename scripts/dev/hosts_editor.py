
from __future__ import annotations

MARKER = "# ft_transcendence:managed"
DEFAULT_IP = "127.0.0.1"


class HostsConflictError(ValueError):
    pass


def find_addresses(content: str, domain: str) -> set[str]:
    addresses = set()

    for line in content.splitlines():
        fields = line.split("#", 1)[0].split()

        if len(fields) < 2:
            continue

        aliases = {name.lower() for name in fields[1:]}

        if domain.lower() in aliases:
            addresses.add(fields[0])

    return addresses


def ensure_mapping(
    content: str,
    domain: str,
    ip: str = DEFAULT_IP,
) -> tuple[str, bool]:
    addresses = find_addresses(content, domain)

    if addresses == {ip}:
        return content, False

    if addresses:
        raise HostsConflictError(
            f"{domain} already points to {sorted(addresses)}"
        )

    newline = "\r\n" if "\r\n" in content else "\n"
    separator = (
        "" if not content or content.endswith(("\n", "\r"))
        else newline
    )

    entry = f"{ip}\t{domain}\t{MARKER}{newline}"

    return content + separator + entry, True


def remove_managed_mapping(
    content: str,
    domain: str,
    ip: str = DEFAULT_IP,
) -> tuple[str, bool]:
    expected = f"{ip}\t{domain}\t{MARKER}"

    lines = content.splitlines(keepends=True)

    remaining = [
        line for line in lines
        if line.rstrip("\r\n") != expected
    ]

    changed = len(remaining) != len(lines)

    return "".join(remaining), changed
