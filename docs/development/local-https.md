# Local HTTPS Development

## Overview

ft_transcendence uses Nginx as the local HTTPS entry point for the React frontend and NestJS backend.

The development environment provides:

- A local domain configured in `config/development.env`
- Trusted development TLS certificates generated with `mkcert`
- HTTPS termination at Nginx
- Automatic HTTP-to-HTTPS redirection
- REST API routing through the same origin
- WebSocket proxy support for Vite HMR and Socket.IO
- Docker Compose orchestration through a root Makefile
- Managed local domain mappings and CA trust

**Primary application URL:** run `python3 -m scripts.dev.configuration origin` from the repository root. The URLs below use the target localhost development settings as examples; use the configured hostname and published ports for your deployment.

The local setup is intended for development only. It is not a production deployment configuration.

## 1. Platform support

| Platform | Current status |
|---|---|
| WSL2 (Linux + Windows browser) | Implemented and end-to-end tested |
| Native Ubuntu 26.04 (amd64) | Guarded bootstrap and Chromium HTTPS acceptance verified |
| Other native Linux releases (including Ubuntu 24.04) | Not independently validated for the new guarded workflow |
| Native Windows | Platform helpers implemented; full end-to-end validation pending |
| macOS | Not yet supported by the domain and CA trust helpers |

Native Ubuntu 26.04 is the validated environment for the new guarded bootstrap and shared NSS trust workflow. Historical WSL2 validation applies to the legacy workflow, not to the new native Linux adapter.

On WSL2, the setup manages both Linux and Windows hosts mappings and CA trust. This is necessary because Windows browsers use Windows name resolution and certificate trust rather than automatically inheriting those settings from WSL.

## 2. Prerequisites

Install these dependencies before running the project:

- Docker with Docker Compose v2
- GNU Make
- Python 3.10 or later
- OpenSSL
- mkcert
- Git

For Windows or WSL2 integration, Windows PowerShell must also be accessible. WSL2 requires `wslpath` and Windows interoperability.

On supported native Ubuntu, `make initialize` installs missing system prerequisites (including `libnss3-tools` for `certutil`). System package installation, Docker setup and creation of the dedicated Nginx TLS group may require sudo. Do not run the bootstrap itself as root. CA generation, authorization and NSS import do not require sudo. The legacy host-only helpers do not replace dependency installation.

Verify the required tools:

```bash
docker --version
docker compose version
make --version
python3 --version
openssl version
mkcert -version
```

On WSL2, also verify Windows PowerShell access:

```bash
powershell.exe -NoProfile -Command '$PSVersionTable.PSVersion'
```

## 3. First-time setup

### 3.1 Clone the repository

```bash
git clone <repository-url>
cd ft_transcendence
```

Use the actual repository URL supplied by the project team.

### 3.2 Validate public configuration

```bash
python3 -m scripts.dev.configuration
```

The tracked `config/development.env` is the single source for the public development domain, published ports and session policy. No `.env` creation is needed for the guarded deployment. Database credentials are provisioned through the protected bootstrap and Vault. The root `.env.example` was retired; do not delete existing private files blindly. Standalone component runs and CI use their own explicit environment (see [backend E2E tests](backend-e2e.md)).

On POSIX hosts, the public configuration must be a regular file (not a symlink), with no other-user write permission. Standard `644` and Ubuntu `664` checkouts are accepted; group-write permission requires a user-private primary group as reported by the local account database. The loader does not change permissions. The optional cutover helper additionally requires operator ownership before writing. This is a file-mode check, not an audit of checkout ownership, directory permissions or extended ACLs. Keep the checkout trusted. Private keys and Vault secrets retain their separate, stricter permission requirements.

### 3.3 Start the project

On a new supported native Ubuntu VM, use `make initialize`. On an already initialized VM, use `make up`, or `make recover` if Vault is sealed. See the [team quick start](../dev-team-quickstart.md) for prerequisites and recovery instructions. Do not use raw Compose to recreate the guarded runtime.

```bash
make initialize  # First install only
make health      # Read-only verification
```

Changing the public configuration is not a live update: existing containers and certificates may require a reviewed cutover. For the original development hostname, see [the guarded localhost cutover](localhost-cutover.md). Never remove volumes or reinitialize Vault to change a hostname or port.

### 3.4 Open the application

Frontend:

`https://localhost:8443/`

Backend health endpoint:

`https://localhost:8443/api/v1/health`

Expected backend response:

```json
{
  "data": {
    "status": "ok",
    "database": "ok"
  }
}
```

On native Ubuntu, application CA trust is installed in the current user's shared Chromium NSS database, not the operating-system trust store. Chrome, Edge and Opera use the same store in the tested versions. The adapter selects an existing `~/.pki/nssdb` first, otherwise `${XDG_DATA_HOME:-$HOME/.local/share}/pki/nssdb`. It does not create a legacy database, install browsers or modify Firefox profiles. NSS trust is verified separately from browser compatibility; if no browser is installed, browser readiness remains unverified.

For an existing deployment with an unrecorded CA, review its fingerprint and run `python3 -m scripts.dev.ca_trust setup` as the ordinary user to authorize it. A changed authorization record is refused. No sudo is needed for this operation. Already installed system trust is preserved.

On native Ubuntu, `make initialize` automatically records and imports both freshly generated application and Vault CAs. It refuses an unrecorded existing CA, even if it is already trusted elsewhere. For a reviewed existing Vault CA, run `python3 -m scripts.dev.vault_ca_trust setup --apply` explicitly. Read-only `make health` and `make plan` require recorded NSS trust for both CAs; `make check` retains its application-only host scope.

The browser should accept the development certificate without a certificate warning once the relevant CA trust has been installed.

If a browser was open before trust installation, restart it if necessary.

## 4. Development architecture

```text
Browser / Client
       |
       | HTTPS :8443
       v
  Nginx :8443
       |
       +-- /            -> Frontend / Vite :5173
       |
       +-- /api/        -> Backend / NestJS :3000
       |
       +-- /socket.io/  -> Backend / Socket.IO
```

Nginx terminates TLS. Communication from Nginx to application services currently uses HTTP inside the Docker network.

HTTP requests sent to port `8080` receive a permanent redirect to HTTPS on port `8443`.

Frontend and API share the same public development origin.

The reverse proxy also forwards appropriate HTTP headers, including `Host`, `X-Forwarded-For`, and `X-Forwarded-Proto`, and supports WebSocket Upgrade requests.

## 5. Makefile commands

| Command | Description |
|---|---|
| `make initialize` | Guarded initial provisioning or restoration of authorized CA trust |
| `make` / `make up` / `make start` | Start existing guarded services without provisioning or CA import |
| `make recover` | Recover existing services after reboot; manual Vault unseal if required |
| `make setup` | Legacy host-only domain, TLS and application CA setup |
| `make check` | Validate domain, certificates and CA trust |
| `make down` | Stop containers without removing them |
| `make clean` | Remove Compose containers and networks, preserving named volumes |
| `make domain-cleanup` | Remove managed hosts entries with confirmation |
| `make cert-cleanup` | Remove project TLS certificate and private key with confirmation |
| `make trust-uninstall` | Remove managed application and Vault CA NSS trust with confirmation (native Ubuntu) |

`make check` validates the local domain and TLS prerequisites. It does not replace Docker container health checks or application endpoint tests.

The `make clean` target does not include `docker compose down -v`, so the project's PostgreSQL named volume is preserved.

## 6. Verify HTTPS

### 6.1 Check local environment

```bash
make check
```

### 6.2 Check containers

```bash
docker ps --filter label=com.docker.compose.project=transcendence-app-dev
```

All expected services should be running, and PostgreSQL should be healthy.

### 6.3 Check Nginx configuration

```bash
docker exec transcendence-app-dev-nginx-1 nginx -t
```

### 6.4 Check HTTP-to-HTTPS redirection

```bash
curl -sS -D - -o /dev/null \
  "http://localhost:8080/api/v1/health?test=1"
```

Expected HTTP status: `308 Permanent Redirect`.

Expected `Location` header:

```text
https://localhost:8443/api/v1/health?test=1
```

### 6.5 Check HTTPS frontend

CLI HTTPS checks use the public application CA explicitly. They do not require
installation of that CA into the OS trust store. Browser trust is a separate
concern. The demo observer's `make demo-watch` and `make demo-watch-plain`
targets provide `NODE_EXTRA_CA_CERTS` at Node startup for HTTPS and Socket.IO.

```bash
curl -q --cacert "$(mkcert -CAROOT)/rootCA.pem" -sS -o /dev/null \
  -w 'HTTP: %{http_code}, TLS verification: %{ssl_verify_result}\n' \
  https://localhost:8443/
```

Expected: HTTP `200`, TLS verification result `0`.

Do not use `curl -k` or `--insecure` for normal verification, since bypassing certificate validation would hide trust configuration problems.

### 6.6 Check HTTPS backend

```bash
curl -q --cacert "$(mkcert -CAROOT)/rootCA.pem" -sS -i \
  https://localhost:8443/api/v1/health
```

Expected: HTTP `200` with the backend health response.

For an invalid API path, use:

```bash
curl -q --cacert "$(mkcert -CAROOT)/rootCA.pem" -sS -i \
  https://localhost:8443/api/v1/nonexistent
```

The application should handle the invalid route rather than returning an Nginx upstream connection error. Inspect the actual response status and body according to the current backend implementation.

### 6.7 Check the certificate

```bash
openssl s_client \
  -connect localhost:8443 \
  -servername localhost \
  -CAfile "$(mkcert -CAROOT)/rootCA.pem" \
  -verify_hostname localhost \
  -verify_return_error \
  -brief </dev/null
```

Expected: successful TLS handshake and `Verification: OK`.

Inspect certificate details:

```bash
openssl x509 \
  -in infra/nginx/certs/cert.pem \
  -noout -subject -issuer -dates -ext subjectAltName
```

The generated certificate includes the following names:

- The configured development domain (if different from localhost)
- `localhost`
- `127.0.0.1`

## 7. Postman and other API clients

Use:

`GET https://localhost:8443/api/v1/health`

Keep TLS certificate verification enabled.

The client must trust the local mkcert CA. If Postman or another client does not use the relevant operating-system trust store, configure its supported CA trust mechanism using the **public CA certificate**, not the private CA key.

Do not disable TLS verification as a permanent workaround.

## 8. Frontend development and Vite HMR

The Docker Compose configuration supports development file synchronization.

To watch frontend source changes while the stack is running:

```bash
FT_DEV_TLS_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/transcendence/dev/tls" \
python3 -m scripts.dev.configuration compose -p transcendence-app-dev \
  -f compose.yml watch --no-up frontend
```

When files under `frontend/src` change, Docker Compose synchronizes them to the frontend container.

Vite HMR uses a secure WebSocket connection through Nginx.

To inspect it:

1. Open `https://localhost:8443/`.
2. Open browser Developer Tools.
3. Navigate to Network → WS.
4. Reload the page.
5. Inspect the Vite HMR connection.

Expected connection URL:

`wss://localhost:8443/...`

A successful WebSocket upgrade returns HTTP `101 Switching Protocols`.

Changing a React component should update the interface without manually reloading the page.

Stop Docker Compose Watch with `Ctrl+C`.

**Socket.IO note:** Nginx forwards `/socket.io/` to the backend, whose Socket.IO gateway is implemented. A connection needs a valid session cookie, and the backend must be given `APP_ORIGIN` (`https://localhost:8443`): see the [realtime topology protocol](../architecture/realtime-topology.md). Testing it through Nginx and ModSecurity is still to do.

## 9. Cleanup and recovery

Stop the project before deliberately removing local certificates or trust:

```bash
make down
```

### Remove managed domain entries

```bash
make domain-cleanup
```

On WSL2, this handles both Linux and Windows managed hosts entries.

Only eligible entries marked as managed by ft_transcendence are removed. Unrelated hosts mappings must be preserved.

Restore:

```bash
make setup
```

### Remove project TLS certificates

```bash
make cert-cleanup
```

This removes:

- `infra/nginx/certs/cert.pem`
- `infra/nginx/certs/key.pem`

It preserves the mkcert Root CA files and system trust settings.

Restore:

```bash
make setup
```

New project certificates will be generated using the existing local CA.

### Remove development CA trust

```bash
make trust-uninstall
```

On native Ubuntu, removal targets only the matching project-managed application and authorized Vault CA entries in the selected NSS database. Each removal requires explicit confirmation; declining the second may leave only the first removed. System CA trust, third-party aliases, other stores and CA/key files are preserved. Removal from NSS does not guarantee that a browser no longer trusts the CA through another mechanism.

On WSL2, the legacy Linux and Windows trust removal adapters still require independent confirmation.

The Windows helper removes the matching CA from `CurrentUser\Root`; it preserves `LocalMachine\Root`.

The local Root CA files and project TLS certificates are preserved.

**Warning:** Removing mkcert CA trust can affect other local development projects using the same CA.

On an initialized native Ubuntu VM with both authorization records intact, restore both NSS entries using the guarded bootstrap:

```bash
make initialize
make check
make health
```

No new CA approval or sudo for CA trust is expected. If Vault is sealed, follow the recovery procedure after restoring the required host configuration; do not reinitialize Vault. Legacy host-only workflows use `make setup` instead.

### Restart after recovery

```bash
make up
```

### Recover from an invalid certificate

The current certificate setup rejects invalid or incomplete existing certificate pairs instead of overwriting them automatically.

After inspecting the reported error and confirming the affected files belong to this project, stop the stack and use the explicit certificate cleanup and setup procedure:

```bash
make down
make cert-cleanup
make setup
make up
```

Automatic replacement of expired or invalid existing certificates is not yet implemented.

## 10. Security considerations

### Development Root CA

mkcert creates a local Certificate Authority for the developer's environment.

Its private key is sensitive. Never commit, upload, distribute, or copy `rootCA-key.pem` to another developer.

Each developer should generate and trust their own local CA and certificates.

Do not share a project-wide development CA private key.

### Project TLS private key

The following directory is ignored by Git:

```text
infra/nginx/certs/
```

Nginx mounts it read-only.

Verify that certificate files are not tracked:

```bash
git ls-files -- infra/nginx/certs/
```

The expected output is empty.

### Direct development ports

The guarded Compose configuration publishes only the Nginx HTTP redirect and HTTPS ports on `127.0.0.1` (defaults `8080` and `8443`). Frontend `5173`, backend `3000` and monitoring service ports are internal to Docker networks, not directly published on the host. The optional Vault UI gateway publishes its separate TLS port on loopback (default `8200`) only while enabled.

This is still a development configuration, not a production security configuration.

### HSTS

HSTS is not enabled by default for this local development environment.

## 11. Development vs production TLS

The local mkcert CA is only for development.

Production deployment requires a separate design covering:

- A publicly trusted certificate for the actual production domain
- Appropriate certificate issuance and renewal, such as an ACME-based solution where applicable
- Secure certificate and private-key storage
- Standard production HTTPS port `443`
- HTTP-to-HTTPS redirection appropriate to the deployment
- Restricted internal service networking
- Monitoring and renewal failure handling
- Production TLS and HTTP security policies

Development-specific ports such as `8443` must not be assumed to be appropriate for production.

The current Makefile, certificate scripts and Nginx configuration should not be treated as a complete production deployment solution.

## 12. Current limitations and follow-up work

The initial implementation was validated on WSL2 with a Windows browser. The new native Ubuntu workflow was validated on a fresh Ubuntu 26.04 (amd64) VM; it is not a new WSL2 or Windows acceptance result.

The following work remains:

- Validate Ubuntu 24.04 (the original issue environment), other native Linux releases and native Windows separately.
- Validate older Chromium versions, Firefox and sandboxed browser packages separately; they are outside the tested NSS adapter scope.
- Complete full application functional compatibility tests for the additional-browser module; TLS page loading alone is not sufficient.
- Implement macOS support.
- Validate non-default public hostnames and ports separately across the complete runtime.
- Add comprehensive dependency and port-conflict preflight diagnostics.
- Implement safe automatic renewal/replacement of existing invalid or expiring certificates.
- Verify authenticated secure session cookie attributes when the relevant backend route is available.
- Complete Socket.IO connectivity testing through Nginx and ModSecurity (the backend gateway is implemented).
- Confirm that the required GitHub Actions jobs pass on the final pull request.

The current release should not claim those capabilities as completed until they have been implemented and verified.

---

## 13. Verified test coverage

### Native Ubuntu acceptance (Issue #162)

Validated on commit `dcb62a1`, Ubuntu 26.04 LTS (amd64), ordinary user, `umask 002`, with no project CAs or NSS databases at baseline. Browsers were installed after initialization.

| Browser (native packages) | Version | Application | Grafana | Vault UI |
|---|---|---|---|---|
| Chrome | 158.0.8127.88 | PASS | PASS | PASS |
| Edge | 158.0.4370.45 | PASS | PASS | PASS |
| Opera | 141.0.6089.33 | PASS | PASS | PASS |

The checks covered HTTPS page loading without visible certificate warnings, not authenticated feature compatibility. Both CA authorization records and NSS entries were prepared automatically before the browsers were installed. The system CA bundle checksum and local CA inventory stayed unchanged, and explicit OpenSSL system-trust checks rejected both project roots.

Additional acceptance results:

- Three repeated startups and two repeated initializations passed without resource or migration-history drift.
- Recovery after reboot succeeded with manual Shamir unseal; project container IDs, network IDs and volume inventory were preserved. The Docker-managed default bridge ID changed.
- Both managed NSS entries were removed with confirmation; repeated removal was a no-op. `make initialize` restored both entries without CA approval or sudo, preserving CA and authorization checksums. Browser HTTPS after restoration was confirmed by the operator. A negative browser test while trust was absent was not independently recorded.
- One transient Prometheus health check failed after trust restoration; subsequent readiness and health checks passed without intervention. The cause was not established.
- On the development VM, 891 `scripts/dev` tests passed at the same implementation commit.

### Historical WSL2 coverage

The following tests passed during the original WSL2 development:

- 77 Python unit tests.
- 11 Windows PowerShell hosts tests.
- Docker Compose configuration validation.
- Nginx configuration validation.
- HTTPS frontend and REST API requests.
- HTTP-to-HTTPS redirect with query-string preservation.
- OpenSSL certificate verification.
- TLS 1.2 and TLS 1.3 connections.
- Vite HMR WebSocket upgrade through WSS.
- Automatic React interface updates through Docker Compose Watch.
- Makefile setup, check, up, down and clean workflows.
- Managed domain cleanup and restoration.
- TLS certificate cleanup and regeneration.
- CA trust removal and restoration.
- PostgreSQL data preservation across Compose container removal.

These results describe the validated development environment and do not substitute for cross-platform verification or CI checks.

---

Related issues: [#68 — Automate local domain and HTTPS/TLS setup](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/68) and [#162 — Browser CA trust](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/162).
