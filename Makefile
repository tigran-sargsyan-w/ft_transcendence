.DEFAULT_GOAL := up

PYTHON ?= python3
COMPOSE := docker compose -f compose.yml
BOOTSTRAP := ./scripts/bootstrap.sh

.PHONY: up start initialize recover health check-all plan acceptance \
        setup check down clean domain-cleanup cert-cleanup trust-uninstall \
        vault-ui-enable vault-ui-disable vault-ui-status

# Default entry point: use the guarded Vault-only startup, never raw Compose up.
up start:
	$(BOOTSTRAP) --start

# First-time guarded development provisioning (no approval prompts).
initialize:
	$(BOOTSTRAP) --initialize

# Trusted restoration after reboot; prompts for Shamir shares when Vault is sealed.
recover:
	$(BOOTSTRAP) --recover

# Read-only end-to-end readiness and failure localization.
health:
	$(BOOTSTRAP) --health-report

check-all:
	$(BOOTSTRAP) --check-all

plan:
	$(BOOTSTRAP) --plan

# Operator-initiated, non-destructive repeated-start acceptance on a healthy VM.
acceptance:
	$(PYTHON) scripts/dev/verify_startup_idempotency.py --runs 3

# Administrative Vault UI only. Never modifies Vault, DB or app containers.
vault-ui-enable:
	$(PYTHON) scripts/dev/bootstrap_vault_ui.py enable --progress

vault-ui-disable:
	$(PYTHON) scripts/dev/bootstrap_vault_ui.py disable --progress

vault-ui-status:
	$(PYTHON) scripts/dev/bootstrap_vault_ui.py status --progress

# Legacy host-only helpers (not a substitute for --initialize).
setup:
	$(PYTHON) -m scripts.dev.domains setup
	$(PYTHON) -m scripts.dev.certificates setup
	$(PYTHON) -m scripts.dev.ca_trust setup

# Host-only checks. Use `make health` to validate the whole application.
check:
	$(PYTHON) -m scripts.dev.bootstrap_host_check

# Legacy raw-Compose maintenance operations: NOT part of trusted recovery.
# These may leave an initialized runtime unable to resume via --start.
down:
	$(COMPOSE) stop

# Removes application containers/networks (volumes preserved). Never use as recovery.
clean:
	$(COMPOSE) down --remove-orphans

# Remove managed domain entries for the current environment
domain-cleanup:
	$(PYTHON) -m scripts.dev.domains cleanup --apply

# Remove project TLS certificate and private key
cert-cleanup:
	$(PYTHON) -m scripts.dev.certificates cleanup --apply

# Remove local CA trust (preserve CA and certificate files)
trust-uninstall:
	$(PYTHON) -m scripts.dev.ca_trust uninstall

# Demo Lab only. The unprefixed `up`, `down`, and `health` targets above
# retain their existing meanings and MUST NOT be used to operate the lab.
DEMO_SCRIPT := ./demo-lab/demo.sh
DEMO_WATCH := backend/scripts/demo-watch.mjs
SERVICE ?= api
EMAIL ?=

.PHONY: demo-help demo-up demo-status demo-fail demo-recover \
        demo-stop demo-start demo-recreate demo-add-worker demo-remove-worker \
        demo-reset demo-logs demo-down demo-watch demo-watch-plain demo-test

# Export values instead of interpolating them into shell source. demo.sh also
# explicitly allowlists service names before invoking any Docker operation.
demo-fail demo-recover demo-stop demo-start demo-recreate: export SERVICE := $(SERVICE)
demo-watch demo-watch-plain: export EMAIL := $(EMAIL)

demo-help:
	$(DEMO_SCRIPT) help

demo-up:
	$(DEMO_SCRIPT) up

demo-status:
	$(DEMO_SCRIPT) status

demo-fail:
	$(DEMO_SCRIPT) fail "$$SERVICE"

demo-recover:
	$(DEMO_SCRIPT) recover "$$SERVICE"

demo-stop:
	$(DEMO_SCRIPT) stop "$$SERVICE"

demo-start:
	$(DEMO_SCRIPT) start "$$SERVICE"

demo-recreate:
	$(DEMO_SCRIPT) recreate "$$SERVICE"

demo-add-worker:
	$(DEMO_SCRIPT) add worker

demo-remove-worker:
	$(DEMO_SCRIPT) remove worker

demo-reset:
	$(DEMO_SCRIPT) reset

demo-logs:
	$(DEMO_SCRIPT) logs

demo-down:
	$(DEMO_SCRIPT) down

# By default show real Graph Engine summaries; EMAIL skips the email prompt.
# Passwords are still entered interactively and are never passed as arguments.
demo-watch:
	@if [ -n "$$EMAIL" ]; then \
		node $(DEMO_WATCH) --analyze --email "$$EMAIL"; \
	else \
		node $(DEMO_WATCH) --analyze; \
	fi

# Plain Socket.IO observation, useful when testing two independent sessions.
demo-watch-plain:
	@if [ -n "$$EMAIL" ]; then \
		node $(DEMO_WATCH) --email "$$EMAIL"; \
	else \
		node $(DEMO_WATCH); \
	fi

# Offline tests only: no Docker operations or demo startup.
demo-test:
	$(PYTHON) -m unittest discover -s demo-lab -p 'test_*.py'
	node --test backend/scripts/demo-watch-format.test.mjs
	bash -n $(DEMO_SCRIPT)
	node --check $(DEMO_WATCH)
	node --check backend/scripts/demo-watch-format.mjs
