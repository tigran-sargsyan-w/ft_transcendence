
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

    def is_managed_entry(line: str) -> bool:
        text = line.rstrip("\r\n")

        entry, separator, comment = text.partition("#")

        # Only remove entries carrying our exact marker.
        if not separator:
            return False

        if comment.strip() != MARKER[1:].strip():
            return False

        # Accept tabs or spaces between fields.
        fields = entry.split()

        # Do not remove lines containing other aliases.
        if len(fields) != 2:
            return False

        return (
            fields[0] == ip
            and fields[1].lower() == domain.lower()
        )

    lines = content.splitlines(keepends=True)

    remaining = [
        line for line in lines
        if not is_managed_entry(line)
    ]

    changed = len(remaining) != len(lines)

    return "".join(remaining), changed

