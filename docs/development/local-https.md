# Local HTTPS Development

## Overview

ft_transcendence uses Nginx as the local HTTPS entry point for the React frontend and NestJS backend.

The development environment provides:

- A local domain: `transcendence.test`
- Trusted development TLS certificates generated with `mkcert`
- HTTPS termination at Nginx
- Automatic HTTP-to-HTTPS redirection
- REST API routing through the same origin
- WebSocket proxy support for Vite HMR and future Socket.IO functionality
- Docker Compose orchestration through a root Makefile
- Managed local domain mappings and CA trust

**Primary application URL:** `https://transcendence.test:8443`

The local setup is intended for development only. It is not a production deployment configuration.

## 1. Platform support

| Platform | Current status |
|---|---|
| WSL2 (Linux + Windows browser) | Implemented and end-to-end tested |
| Native Linux | Implemented; full end-to-end validation pending |
| Native Windows | Platform helpers implemented; full end-to-end validation pending |
| macOS | Not yet supported by the domain and CA trust helpers |

WSL2 is the validated development environment.

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

The scripts do not automatically install external dependencies.

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

### 3.2 Configure environment variables

Create `.env` from the provided example:

```bash
cp .env.example .env
```

Do not overwrite an existing `.env` containing settings you intend to preserve.

Review the database credentials and other local development settings before proceeding.

Never commit `.env` or share actual credentials in logs.

### 3.3 Start the project

From the repository root:

```bash
make
```

This is equivalent to `make up`.

The command performs the following operations:

1. Inspects the local domain mappings.
2. Requests authorization before modifying hosts files when necessary.
3. Checks for existing development TLS certificates.
4. Generates missing certificates with `mkcert`.
5. Checks the required local CA trust.
6. Requests authorization before installing missing trust.
7. Builds and starts the Docker Compose services.

On WSL2, Windows UAC may appear when the Windows hosts file needs modification. Linux hosts changes may require `sudo`.

Trust installation is a security-sensitive operation and requires explicit confirmation.

After the initial configuration, repeated `make` commands reuse valid domain mappings, certificates, and trust settings without unnecessary regeneration.

### 3.4 Open the application

Frontend:

`https://transcendence.test:8443/`

Backend health endpoint:

`https://transcendence.test:8443/api/v1/health`

Expected backend response:

```json
{
  "data": {
    "status": "ok",
    "database": "ok"
  }
}
```

The browser should accept the development certificate without a certificate warning once the relevant CA trust has been installed.

If a browser was open before trust installation, restart it if necessary.

## 4. Development architecture

```text
Browser / Client
       |
       | HTTPS :8443
       v
  Nginx :443
       |
       +-- /            -> Frontend / Vite :5173
       |
       +-- /api/        -> Backend / NestJS :3000
       |
       +-- /socket.io/  -> Backend / future Socket.IO
```

Nginx terminates TLS. Communication from Nginx to application services currently uses HTTP inside the Docker network.

HTTP requests sent to port `8080` receive a permanent redirect to HTTPS on port `8443`.

Frontend and API share the same public development origin.

The reverse proxy also forwards appropriate HTTP headers, including `Host`, `X-Forwarded-For`, and `X-Forwarded-Proto`, and supports WebSocket Upgrade requests.

## 5. Makefile commands

| Command | Description |
|---|---|
| `make` / `make up` | Prepare the environment, build and start services |
| `make setup` | Configure domain, TLS certificates and CA trust |
| `make check` | Validate domain, certificates and CA trust |
| `make down` | Stop containers without removing them |
| `make clean` | Remove Compose containers and networks, preserving named volumes |
| `make domain-cleanup` | Remove managed hosts entries with confirmation |
| `make cert-cleanup` | Remove project TLS certificate and private key with confirmation |
| `make trust-uninstall` | Remove local mkcert CA trust with confirmation |

`make check` validates the local domain and TLS prerequisites. It does not replace Docker container health checks or application endpoint tests.

The `make clean` target does not include `docker compose down -v`, so the project's PostgreSQL named volume is preserved.

## 6. Verify HTTPS

### 6.1 Check local environment

```bash
make check
```

### 6.2 Check containers

```bash
docker compose -f compose.yml ps
```

All expected services should be running, and PostgreSQL should be healthy.

### 6.3 Check Nginx configuration

```bash
docker compose -f compose.yml exec nginx nginx -t
```

### 6.4 Check HTTP-to-HTTPS redirection

```bash
curl -sS -D - -o /dev/null \
  "http://transcendence.test:8080/api/v1/health?test=1"
```

Expected HTTP status: `308 Permanent Redirect`.

Expected `Location` header:

```text
https://transcendence.test:8443/api/v1/health?test=1
```

### 6.5 Check HTTPS frontend

```bash
curl -sS -o /dev/null \
  -w 'HTTP: %{http_code}, TLS verification: %{ssl_verify_result}\n' \
  https://transcendence.test:8443/
```

Expected: HTTP `200`, TLS verification result `0`.

Do not use `curl -k` or `--insecure` for normal verification, since bypassing certificate validation would hide trust configuration problems.

### 6.6 Check HTTPS backend

```bash
curl -sS -i \
  https://transcendence.test:8443/api/v1/health
```

Expected: HTTP `200` with the backend health response.

For an invalid API path, use:

```bash
curl -sS -i \
  https://transcendence.test:8443/api/v1/nonexistent
```

The application should handle the invalid route rather than returning an Nginx upstream connection error. Inspect the actual response status and body according to the current backend implementation.

### 6.7 Check the certificate

```bash
openssl s_client \
  -connect transcendence.test:8443 \
  -servername transcendence.test \
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

- `transcendence.test`
- `localhost`
- `127.0.0.1`

## 7. Postman and other API clients

Use:

`GET https://transcendence.test:8443/api/v1/health`

Keep TLS certificate verification enabled.

The client must trust the local mkcert CA. If Postman or another client does not use the relevant operating-system trust store, configure its supported CA trust mechanism using the **public CA certificate**, not the private CA key.

Do not disable TLS verification as a permanent workaround.

## 8. Frontend development and Vite HMR

The Docker Compose configuration supports development file synchronization.

To watch frontend source changes while the stack is running:

```bash
docker compose -f compose.yml watch --no-up frontend
```

When files under `frontend/src` change, Docker Compose synchronizes them to the frontend container.

Vite HMR uses a secure WebSocket connection through Nginx.

To inspect it:

1. Open `https://transcendence.test:8443/`.
2. Open browser Developer Tools.
3. Navigate to Network → WS.
4. Reload the page.
5. Inspect the Vite HMR connection.

Expected connection URL:

`wss://transcendence.test:8443/...`

A successful WebSocket upgrade returns HTTP `101 Switching Protocols`.

Changing a React component should update the interface without manually reloading the page.

Stop Docker Compose Watch with `Ctrl+C`.

**Socket.IO note:** Nginx already includes the `/socket.io/` forwarding configuration. The backend Socket.IO Gateway has not yet been implemented, so full Socket.IO connectivity testing is deferred. HTTP 404 is currently expected for the standard Socket.IO handshake endpoint.

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

On WSL2, Linux and Windows trust removal require independent confirmation.

The Windows helper removes the matching CA from `CurrentUser\Root`; it preserves `LocalMachine\Root`.

The local Root CA files and project TLS certificates are preserved.

**Warning:** Removing mkcert CA trust can affect other local development projects using the same CA.

Restore:

```bash
make setup
make check
```

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

The current Compose configuration intentionally retains development/debug access to services.

In particular, Frontend port `5173` and Backend port `3000` are published separately from Nginx.

**These direct HTTP ports bypass TLS termination at Nginx.**

This is a development exception, not a production security configuration.

Production deployment must restrict internal services, remove unnecessary published debug ports, and prevent external clients from bypassing the HTTPS reverse proxy.

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

The initial implementation has been validated end-to-end on WSL2 with a Windows browser.

The following work remains:

- Complete native Linux and native Windows end-to-end validation.
- Implement macOS support.
- Make the domain and published ports consistently configurable across hosts management, certificate issuance, Vite and Nginx.
- Add comprehensive dependency and port-conflict preflight diagnostics.
- Implement safe automatic renewal/replacement of existing invalid or expiring certificates.
- Verify authenticated secure session cookie attributes when the relevant backend route is available.
- Complete Socket.IO connectivity testing once the backend Gateway is implemented.
- Confirm that the required GitHub Actions jobs pass on the final pull request.

The current release should not claim those capabilities as completed until they have been implemented and verified.

---

## 13. Verified test coverage

The following tests have passed during WSL2 development:

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

Related issue: [#68 — Automate local domain and HTTPS/TLS setup](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/68).
