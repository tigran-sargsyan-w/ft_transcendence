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
