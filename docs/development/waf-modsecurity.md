# Nginx + ModSecurity WAF

## Overview

The development HTTPS gateway uses the pinned
`owasp/modsecurity-crs:4.30.0-nginx-202611090911` image.

ModSecurity and OWASP Core Rule Set (CRS) protect the frontend,
REST API and Socket.IO endpoint.

The WAF runs in blocking mode:

- `MODSEC_RULE_ENGINE=On`
- `BLOCKING_PARANOIA=1`
- Allowed methods: GET, HEAD, POST, OPTIONS, PUT, PATCH, DELETE
- HTTPS: `127.0.0.1:8443`
- HTTP redirect: `127.0.0.1:8080`

Configuration:

- `compose.yml`
- `infra/nginx/default.conf.template`
- `scripts/dev/bootstrap_nginx.py`

## Secret-safe development logging (#161)

The gateway uses a minimal native ModSecurity audit format (`AZ`).
This retains transaction metadata and record boundaries, rather than
request/response headers, bodies or matched values. The JSON audit
format is intentionally avoided because it can retain the request URI
independently of the selected audit parts. WAF blocking, request and
response inspection, and scoped Socket.IO/Grafana rules are unchanged.

Both HTTP and HTTPS servers use `safe_main` access logs: remote address,
timestamp, response status, bytes sent and request duration. Raw URLs,
query strings, referer and other request headers are not included.
Server error logging is restricted to `crit`; the Grafana-specific
logging protection is retained.

Trade-off: detailed CRS rule IDs and matched data are not available in
minimal audit records. HTTP status and transaction metadata remain.
This is a development hardening policy, not a comprehensive redaction
pipeline. Critical errors and messages outside the server context may
still contain sensitive data; other application logs are out of scope.

Existing installations must explicitly recreate only the trusted Nginx
service after updating the repository; `make start` and `make recover`
preserve existing containers and do not apply new environment settings.
Validate the generated configuration and run `make health` afterward.
Never delete Vault/PostgreSQL volumes to apply a logging change.

Regression coverage: `python3 -m unittest discover -s scripts/dev`.
For live checks, use unique synthetic cookie, authorization, body and
query markers with successful and blocked requests; report only counts
and HTTP statuses, not raw logs. Verify SQLi/XSS blocking still works.
Previously collected logs are not sanitized by this change: restrict
access, review retention and revoke any exposed live sessions as needed.

## Socket.IO compatibility

The proxy supports both Socket.IO transports:

- WebSocket, including HTTP Upgrade
- Engine.IO HTTP long-polling GET and POST

OWASP CRS rule `920420` normally rejects the `text/plain`
content type used by Engine.IO polling POST requests.

The Nginx configuration defines local rule `1000043` to remove
rule `920420` only when all conditions match:

1. The request is inside `/socket.io/`.
2. The HTTP method is POST.
3. The query parameter `transport` equals `polling`.
4. The Content-Type is `text/plain`, optionally with UTF-8 charset.

This is a narrowly scoped compatibility exception, not a
global CRS bypass.

Other CRS protections remain active, including SQL injection
and XSS detection.

## TLS private-key access

The WAF container runs as UID 101, with supplementary
group GID 43043 (`ft-transcendence-waf-tls`).

Ubuntu may assign host UID 101 to the unrelated `uuidd`
system account. Do not grant TLS access using a host ACL
for UID 101.

Instead:

- The certificate directory grants group 43043 traversal.
- The certificate and private key grant group 43043 read access.
- Docker Compose adds supplementary group 43043 to Nginx.
- The private key remains inaccessible to other users.
- TLS files are mounted read-only.

Implementation:

- `scripts/dev/nginx_tls_access.py`
- `scripts/bootstrap.sh`
- `scripts/install-deps.sh`

On a fresh Ubuntu VM, install prerequisites and use the
normal confirmed `--prepare-host` bootstrap stage to create
the dedicated group and configure the ACL.

An existing group with an unexpected GID, or a GID collision,
must be reviewed rather than silently reassigned.

## Read-only verification

Verify host prerequisites and TLS access:

    ./scripts/bootstrap.sh --check-host
    python3 -m scripts.dev.nginx_tls_access check

Verify the running development stack:

    make health

Verify the active gateway:

    docker inspect \
      --format 'Image={{.Config.Image}} GroupAdd={{json .HostConfig.GroupAdd}} Health={{.State.Health.Status}}' \
      transcendence-app-dev-nginx-1

Expected values include `GroupAdd=["43043"]` and
`Health=healthy`.

The normal guarded startup must not automatically recreate
an unexpected existing gateway.

## Regression coverage

Run the Nginx and TLS access unit tests:

    PYTHONPATH=scripts/dev python3 -m unittest \
      scripts/dev/test_bootstrap_nginx.py \
      scripts/dev/test_nginx_tls_access.py -q

Validation completed during issue #43:

- 66 Python unit tests passed.
- Docker Compose and Nginx configuration checks passed.
- Benign frontend and backend requests returned HTTP 200.
- SQLi and XSS probes were blocked with HTTP 403.
- Both Socket.IO transports reached the real backend and
  received the expected unauthenticated rejection.
- A temporary mock backend verified successful Socket.IO
  handshakes, messages, acknowledgements, reconnect and
  resubscription through the WAF.
- The active WAF gateway passed the full development health
  report with the supplementary TLS group enabled.
- Nine other application containers retained their identities
  during the TLS group migration.

The mock-authentication test does not replace application
authentication E2E coverage.

Fresh-VM bootstrap implementation has been tested through
unit tests and on an existing VM. An independent first-install
test on a clean VM remains to be performed.

## Team migration: legacy Nginx to WAF

After the PR merges into `main`, teammates with an already initialized VM
running `nginx:1.28-alpine` must migrate the existing gateway. **Do not use
`make up` to replace an old Nginx**; normal startup rejects an unknown image
or permissions configuration by design.

From the project root, after updating `main`:

```sh
./scripts/bootstrap.sh --plan-waf-upgrade
./scripts/bootstrap.sh --upgrade-waf
make health
```

`--plan-waf-upgrade` is read-only. `--upgrade-waf` installs missing host
prerequisites with explicit confirmation, then validates the exact old
Compose Nginx identity, checks TLS and application readiness, and asks for
the confirmation phrase `UPGRADE-WAF` before creating the dedicated TLS
group/ACL and recreating only the `nginx` service. It waits for Docker health,
checks API and OWASP CRS blocking, and compares all other application/Vault
container identities. **No PostgreSQL, Vault, other services, or named
volumes are recreated.**

If the WAF is already correctly installed, the command verifies it and
returns without recreating any containers. Unexpected existing containers
are refused instead of overwritten.

On failure after gateway recreation begins, the upgrade attempts to restore
the verified legacy Nginx configuration (only the gateway), checks HTTPS/API,
and restores the tracked target Compose file. Inspect the outcome before
running any other startup operation; a rolled-back legacy container will not
satisfy the new WAF-only `make up` guard until migration is retried. The
host's newly created TLS group/ACL may remain after a failed upgrade, which
is safe and idempotent. Rollback is best-effort and must be verified.

This upgrade is exclusively for a trusted existing `nginx:1.28-alpine` gateway.
Fresh machines use the usual `make initialize` bootstrap. No existing
Vault/PostgreSQL volume or container should be deleted to perform the upgrade.

On WSL2 with a Windows browser, VS Code may lose its forwarded `8443` port
after Nginx recreation. If `make health` succeeds but the browser cannot
connect, inspect the VS Code **Ports** tab and restore forwarding for `8443`.

## Operations and rollback

Do not use `docker compose down -v`, broad recreation or
volume deletion to troubleshoot the WAF.

The initial gateway migration and TLS-group migration were
performed as controlled single-service operations with
identity checks, health verification and rollback handling.

Future gateway upgrades should use an explicitly reviewed
single-service procedure, preserving Vault, PostgreSQL and
other application services.

Do not commit temporary operator migration scripts or local
TLS certificates and private keys.
