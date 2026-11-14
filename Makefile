.DEFAULT_GOAL := up

PYTHON ?= python3
COMPOSE := docker compose -f compose.yml
BOOTSTRAP := ./scripts/bootstrap.sh

.PHONY: up start initialize recover health check-all plan acceptance \
        setup check down clean domain-cleanup cert-cleanup trust-uninstall

# Default entry point: use the guarded Vault-only startup, never raw Compose up.
up start:
	$(BOOTSTRAP) --start

# First-time operator-approved provisioning (interactive; not for routine use).
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

# Legacy host-only helpers (not a substitute for --initialize).
setup:
	$(PYTHON) -m scripts.dev.domains setup
	$(PYTHON) -m scripts.dev.certificates setup
	$(PYTHON) -m scripts.dev.ca_trust setup

# Host-only checks. Use `make health` to validate the whole application.
check:
	$(PYTHON) -m scripts.dev.domains check
	$(PYTHON) -m scripts.dev.certificates check
	$(PYTHON) -m scripts.dev.ca_trust check

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
