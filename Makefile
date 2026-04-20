# Polymarket Insider Tracker — dev workflow entry points.
#
# `make install` is the bootstrap: it probes DNS, patches /etc/hosts
# (via sudo) if Polymarket hostnames are being hijacked upstream,
# installs Python deps with uv, and runs the alembic migration head.
#
# Most other targets assume `install` has already run.

SHELL := /bin/bash
PYTHON ?= python3
UV ?= uv

PROJECT := polymarket-insider-tracker
CAPTURES_DIR := data/captures
TODAY := $(shell date -u +%Y-%m-%dT%H%M%S)

# Env file — loaded by DATABASE_URL-dependent targets so alembic
# picks up the right connection string without the operator having
# to remember `set -a; source .env`.
ENV_FILE := .env
LOAD_ENV = set -a; [[ -f $(ENV_FILE) ]] && source $(ENV_FILE); set +a

.DEFAULT_GOAL := help

# ── Help ──────────────────────────────────────────────────────────
.PHONY: help
help:
	@awk 'BEGIN{FS=":.*##"; printf "Targets:\n"} \
	  /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' \
	  $(MAKEFILE_LIST)

# ── Bootstrap ─────────────────────────────────────────────────────
.PHONY: install
install: dns-fix deps migrate ## Full bootstrap: DNS + uv sync + migrations

.PHONY: deps
deps: ## Sync Python deps with uv
	$(UV) sync --extra dev

.PHONY: migrate
migrate: ## Run alembic upgrade head
	$(LOAD_ENV); $(UV) run alembic upgrade head

# ── DNS hygiene ───────────────────────────────────────────────────
.PHONY: dns-check
dns-check: ## Probe Polymarket DNS; exits non-zero if any host is hijacked
	@$(PYTHON) scripts/dns-probe-patch.py

.PHONY: dns-fix
dns-fix: ## Patch /etc/hosts (re-execs under sudo) if a hijack is found
	@if ! $(PYTHON) scripts/dns-probe-patch.py --quiet; then \
	  echo "dns-fix: hijack detected, applying patch (needs sudo) ..."; \
	  sudo $(PYTHON) scripts/dns-probe-patch.py --apply; \
	else \
	  echo "dns-fix: nothing to do."; \
	fi

.PHONY: dns-revert
dns-revert: ## Remove the managed /etc/hosts block
	sudo $(PYTHON) scripts/dns-probe-patch.py --revert

# ── Quality gates ─────────────────────────────────────────────────
.PHONY: test
test: ## Run the full pytest suite
	$(LOAD_ENV); $(UV) run pytest -q

.PHONY: test-scenarios
test-scenarios: ## Run the scenario-level E2E tests only
	$(LOAD_ENV); $(UV) run pytest tests/scenarios -q

.PHONY: lint
lint: ## ruff check + format check
	$(UV) run ruff check src/ tests/ scripts/
	$(UV) run ruff format --check src/ tests/ scripts/

.PHONY: type-check
type-check: ## mypy src
	$(UV) run mypy src/

# ── Pipeline smoke ────────────────────────────────────────────────
.PHONY: capture
capture: ## 2-minute live CLOB capture → data/captures/
	@mkdir -p $(CAPTURES_DIR)
	$(LOAD_ENV); $(UV) run python scripts/direct-capture.py \
	  --output $(CAPTURES_DIR)/live-$(TODAY).jsonl \
	  --duration 120 --market-limit 200

.PHONY: replay
replay: ## Replay the most recent capture through the detector stack
	@latest=$$(ls -1t $(CAPTURES_DIR)/live-*.jsonl 2>/dev/null | head -1); \
	if [[ -z "$$latest" ]]; then echo "no captures — run \`make capture\`"; exit 1; fi; \
	echo "replaying $$latest"; \
	$(LOAD_ENV); $(UV) run python -m polymarket_insider_tracker.backtest \
	  --capture "$$latest" --window-days 1

.PHONY: sanity-band
sanity-band: ## Run the detector-metrics sanity-band gate
	$(LOAD_ENV); $(UV) run python scripts/sanity-band-check.py --days 7

.PHONY: snapshot
snapshot: ## Render the daily snapshot markdown + PDF (no email)
	$(LOAD_ENV); $(UV) run python scripts/send-report.py --no-send

.PHONY: newsletter-test
newsletter-test: ## Send the legacy market-snapshot to configured targets and fail if the send didn't land
	@$(LOAD_ENV); \
	out=$$( $(UV) run python scripts/send-report.py 2>&1 ); \
	echo "$$out"; \
	if echo "$$out" | grep -qE 'Batch complete: [1-9][0-9]*/[1-9][0-9]* sent, 0 failed'; then \
	  echo "newsletter-test: delivery succeeded"; \
	else \
	  echo "newsletter-test: delivery did not succeed — see output above" >&2; \
	  exit 1; \
	fi

.PHONY: newsletter-daily
newsletter-daily: ## Send the Phase N1 daily (alert-led layout) to configured targets
	@$(LOAD_ENV); \
	out=$$( $(UV) run python scripts/newsletter-daily.py 2>&1 ); \
	echo "$$out"; \
	if echo "$$out" | grep -qE 'Batch complete: [1-9][0-9]*/[1-9][0-9]* sent, 0 failed'; then \
	  echo "newsletter-daily: delivery succeeded"; \
	else \
	  echo "newsletter-daily: delivery did not succeed — see output above" >&2; \
	  exit 1; \
	fi

# ── Hygiene ───────────────────────────────────────────────────────
.PHONY: clean
clean: ## Remove build + test artefacts (does not touch captures)
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage \
	  reports build dist *.egg-info src/*.egg-info

.PHONY: clean-captures
clean-captures: ## Wipe data/captures/ (CAPTURES ARE GITIGNORED, but confirm first)
	@read -p "Delete all captures under $(CAPTURES_DIR)? [y/N] " ans; \
	  [[ "$$ans" == "y" ]] && rm -rf $(CAPTURES_DIR) || echo "aborted."
