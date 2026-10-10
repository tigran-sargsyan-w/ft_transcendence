.DEFAULT_GOAL := up

PYTHON ?= python3
COMPOSE := docker compose -f compose.yml

.PHONY: up setup check down clean domain-cleanup cert-cleanup trust-uninstall

# Prepare the local environment and start containers
up: setup
	$(COMPOSE) up -d --build

# Configure domain, TLS certificates and CA trust
setup:
	$(PYTHON) -m scripts.dev.domains setup
	$(PYTHON) -m scripts.dev.certificates setup
	$(PYTHON) -m scripts.dev.ca_trust setup

# Verify the local environment
check:
	$(PYTHON) -m scripts.dev.domains check
	$(PYTHON) -m scripts.dev.certificates check
	$(PYTHON) -m scripts.dev.ca_trust check

# Stop containers without removing them
down:
	$(COMPOSE) stop

# Remove project containers and networks, preserve volumes
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
