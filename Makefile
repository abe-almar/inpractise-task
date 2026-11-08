# The one command is `make run`. It works from a fresh clone: it starts the local database, installs the job, loads
# everything the first time and only what is new after that. Safe to call every morning; a non-zero exit means the
# run or a hard check failed. Every target is a thin wrapper over the `sec-filings` command (src/sec_filings/cli.py).

# Where the database is: the environment wins, then .env, then the local one in docker-compose.yml.
LOCAL_DATABASE_URL := postgresql://sec:sec@localhost:5432/sec_filings
DATABASE_URL ?= $(or $(shell [ -f .env ] && . ./.env && echo "$$DATABASE_URL"),$(LOCAL_DATABASE_URL))
export DATABASE_URL

PYTHON ?= python3.12
CLI := .venv/bin/sec-filings
INSTALLED := .venv/.installed
PERIOD ?= FY

.DEFAULT_GOAL := help
.PHONY: help run backfill check report summary verify-labels test test-db setup db

help:  ## List the targets
	@awk -F':.*## ' '/^[a-z-]+:.*## / {printf "%-16s%s\n", $$1, $$2}' $(MAKEFILE_LIST)

run: setup db  ## THE ONE COMMAND: load, label and check; exit non-zero if a hard check fails
	@$(CLI) run

backfill: setup db  ## Run, and also re-read the history of filers with a coverage gap (FULL=1: of every filer)
	@$(CLI) backfill $(if $(FULL),--full)

check: setup db  ## Reprint the latest run's checks, no fetching; fails if no clean run today
	@$(CLI) check

report: setup db  ## One report: make report TICKER=AAPL YEAR=2009 PERIOD=FY
	@$(CLI) report $(TICKER) $(YEAR) $(PERIOD)

summary: setup db  ## One line per company
	@$(CLI) summary

verify-labels: setup db  ## Reload the SEC's labels for every filer; fails on a real disagreement
	@$(CLI) verify-labels

test: setup  ## Offline unit tests
	@.venv/bin/pytest -q

test-db: setup db  ## The replay tests against Postgres (each in its own throwaway schema)
	@TEST_DATABASE_URL="$$DATABASE_URL" .venv/bin/pytest -q tests/test_replay_db.py

setup: $(INSTALLED)  ## Create the virtualenv and install; does nothing when already done

$(INSTALLED): pyproject.toml
	$(PYTHON) -m venv .venv
	.venv/bin/pip install --quiet -e ".[dev]"
	@touch $@

db:  ## Start the local database; does nothing when DATABASE_URL points elsewhere
ifeq ($(DATABASE_URL),$(LOCAL_DATABASE_URL))
	@docker compose up -d --wait --quiet-pull
endif
