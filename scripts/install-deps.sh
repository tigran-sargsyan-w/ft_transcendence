#!/usr/bin/env bash
# Developer prerequisites for ft_transcendence (Ubuntu only).
# Does not create Docker containers, volumes, Vault secrets or certificates.
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/install-deps.sh [--check|--help]

  (default)   Install missing Ubuntu packages with confirmation.
  --check     Read-only prerequisites report; no sudo or mutations.
  --help      Print this help.

Run as your normal Ubuntu user, not with sudo. Docker group access
may require signing out and signing in again.
EOF
}

MODE=install
case "${1:-}" in
  "") ;;
  --check) MODE=check ;;
  --help|-h) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
if (( $# > 1 )); then usage >&2; exit 2; fi

say() { printf '%s\n' "$*"; }
fail() { say "ERROR: $*" >&2; exit 1; }
command_exists() { command -v "$1" >/dev/null 2>&1; }
pkg_installed() { dpkg-query -W -f='${db:Status-Abbrev}' "$1" 2>/dev/null | grep -q '^ii'; }

confirm() {
  local reply
  if [[ ! -t 0 ]]; then
    fail "Interactive confirmation required. Run this script in a terminal."
  fi
  printf '%s [y/N] ' "$1"
  IFS= read -r reply || return 1
  [[ "$reply" == y || "$reply" == Y || "$reply" == yes || "$reply" == YES ]]
}

[[ -r /etc/os-release ]] || fail "Cannot identify the operating system"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == ubuntu ]] || fail "Supported platform: Ubuntu only (found ${ID:-unknown})"
case "${VERSION_ID:-}" in
  22.04|24.04|26.04|26.10) ;;
  *) fail "Unsupported Ubuntu release: ${VERSION_ID:-unknown}. Review Docker support before installing." ;;
esac
[[ $(id -u) != 0 ]] || fail "Run as a regular user, not root or sudo"
command_exists dpkg-query || fail "dpkg-query is required"
command_exists apt-get || fail "apt-get is required"

say "=== ft_transcendence prerequisites: Ubuntu $VERSION_ID ($(dpkg --print-architecture)) ==="

base_packages=(git make python3 openssl curl ca-certificates util-linux mkcert libnss3-tools)
missing_base=()
for package in "${base_packages[@]}"; do
  if pkg_installed "$package"; then
    say "OK: $package"
  else
    say "MISSING: $package"
    missing_base+=("$package")
  fi
done

# A valid Docker client and Compose plugin may already come from a user's setup.
if command_exists docker && docker --version >/dev/null 2>&1; then
  say "OK: Docker CLI"
  docker_cli=1
else
  say "MISSING: Docker CLI"
  docker_cli=0
fi
if (( docker_cli )) && docker compose version >/dev/null 2>&1; then
  say "OK: Docker Compose plugin"
  compose_ok=1
else
  say "MISSING: Docker Compose plugin"
  compose_ok=0
fi
if (( docker_cli )) && docker buildx version >/dev/null 2>&1; then
  say "OK: Docker Buildx plugin"
  buildx_ok=1
else
  say "MISSING: Docker Buildx plugin"
  buildx_ok=0
fi

docker_packages_needed=0
if (( !docker_cli || !compose_ok || !buildx_ok )); then
  docker_packages_needed=1
fi

if (( docker_packages_needed )); then
  # Never replace existing Docker distributions or container runtimes without review.
  conflicts=()
  for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
    if pkg_installed "$package"; then conflicts+=("$package"); fi
  done
  if (( ${#conflicts[@]} )); then
    fail "Conflicting existing packages: ${conflicts[*]}. Refusing to replace a Docker installation automatically."
  fi
  if (( docker_cli )) && ! pkg_installed docker-ce-cli; then
    fail "Docker CLI exists but is not from the official Docker CE APT packages; review installation manually."
  fi
fi

if [[ "$MODE" == check ]]; then
  issues=0
  (( ${#missing_base[@]} == 0 )) || issues=1
  (( !docker_packages_needed )) || issues=1
  if (( docker_cli )); then
    if docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
      say "OK: Docker daemon accessible without sudo"
    else
      say "NOT READY: Docker daemon inaccessible (service or user permissions)"
      issues=1
    fi
  fi
  if (( issues )); then
    say "RESULT: PREREQUISITES NOT READY (read-only)"
    exit 1
  fi
  say "RESULT: PREREQUISITES READY (read-only)"
  exit 0
fi

if (( ${#missing_base[@]} || docker_packages_needed )); then
  say ""
  say "Missing packages will be installed through Ubuntu APT and (if needed) Docker's official APT repository."
  say "No existing Docker installation, containers, images or volumes will be removed."
  if ! confirm "Proceed with OS package installation?"; then
    say "CANCELLED: No packages installed"
    exit 1
  fi
  command_exists sudo || fail "sudo is required for package installation"
  sudo -v

  # Install common Ubuntu tools only if needed.
  if (( ${#missing_base[@]} )); then
    sudo apt-get update
    if ! pkg_installed mkcert && ! apt-cache show mkcert >/dev/null 2>&1; then
      say "INFO: Enabling Ubuntu universe repository for mkcert"
      sudo apt-get install -y software-properties-common
      sudo add-apt-repository -y universe
      sudo apt-get update
    fi
    sudo apt-get install -y "${missing_base[@]}"
  fi

  if (( docker_packages_needed )); then
    # Docker repository requires curl and CA certificates (installed above).
    [[ -e /etc/apt/sources.list.d/docker.sources ]] && {
      [[ -s /etc/apt/sources.list.d/docker.sources ]] || fail "Existing Docker .sources file is empty"
    }
    if [[ ! -e /etc/apt/sources.list.d/docker.sources ]]; then
      # Avoid creating duplicate or overriding an existing manually configured repository.
      if grep -Rqs 'download.docker.com/linux/ubuntu' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
        fail "An existing Docker APT source was detected; review it instead of adding another."
      fi
      sudo install -m 0755 -d /etc/apt/keyrings
      if [[ ! -s /etc/apt/keyrings/docker.asc ]]; then
        tmpkey=$(mktemp)
        trap 'rm -f "${tmpkey:-}"' EXIT
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o "$tmpkey"
        sudo install -m 0644 "$tmpkey" /etc/apt/keyrings/docker.asc
        rm -f "$tmpkey"
        trap - EXIT
      fi
      arch=$(dpkg --print-architecture)
      codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
      [[ "$codename" =~ ^[a-z]+$ ]] || fail "Invalid Ubuntu codename"
      tmp_source=$(mktemp)
      trap 'rm -f "${tmp_source:-}"' EXIT
      cat > "$tmp_source" <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $codename
Components: stable
Architectures: $arch
Signed-By: /etc/apt/keyrings/docker.asc
EOF
      sudo install -m 0644 "$tmp_source" /etc/apt/sources.list.d/docker.sources
      rm -f "$tmp_source"
      trap - EXIT
    fi
    say "INFO: Installing official Docker Engine / Compose / Buildx"
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
fi

# Service management is separate from modifying Docker configuration or storage.
if ! command_exists systemctl; then
  fail "systemctl unavailable; Docker service must be started manually"
fi
if ! systemctl is-active --quiet docker; then
  say "INFO: Starting Docker service"
  command_exists sudo || fail "sudo is required to start Docker"
  sudo systemctl enable --now docker
fi

# No automatic root-equivalent group enrollment.
if ! docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
  if ! command_exists docker; then fail "Docker CLI still missing"; fi
  if ! sudo docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
    fail "Docker daemon is not responding even via sudo; inspect systemctl status docker"
  fi
  say ""
  say "Docker works through sudo, but this user cannot access its socket."
  say "SECURITY: Membership in the docker group effectively grants root privileges."
  if id -nG | tr ' ' '\n' | grep -qx docker; then
    fail "Docker group is active but access is denied; review Docker socket permissions manually."
  fi
  command_exists getent || fail "getent is required to inspect group membership"
  getent group docker >/dev/null || fail "Docker group is absent; review Docker installation"
  if id -nG "$(id -un)" | tr ' ' '\n' | grep -qx docker; then
    say "ACTION REQUIRED: Docker group membership exists but is not active in this session."
  elif confirm "Add $(id -un) to the root-equivalent docker group?"; then
    sudo usermod -aG docker "$(id -un)"
    say "ACTION REQUIRED: Sign out and back in (or reboot the VM), then rerun:"
  else
    say "ACTION REQUIRED: You declined Docker group access. Configure rootless Docker or operator-approved access."
  fi
  say "  ./scripts/install-deps.sh --check"
  exit 2
fi

say "=== Final verification ==="
missing=0
for package in "${base_packages[@]}"; do
  if ! pkg_installed "$package"; then say "FAIL: $package"; missing=1; fi
done
if ! docker --version >/dev/null 2>&1; then say "FAIL: Docker CLI"; missing=1; fi
if ! docker compose version >/dev/null 2>&1; then say "FAIL: Docker Compose plugin"; missing=1; fi
if ! docker buildx version >/dev/null 2>&1; then say "FAIL: Docker Buildx plugin"; missing=1; fi
(( missing == 0 )) || fail "One or more dependencies still missing"
say "PASS: All base packages installed"
say "PASS: Docker Engine, Compose and Buildx available"
say "PASS: Docker daemon accessible without sudo"
say "RESULT: DEPENDENCIES READY"
