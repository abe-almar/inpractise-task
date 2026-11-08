# The one command is `make run`. It works from a fresh clone: it starts the local database, installs the job, loads
# everything the first time and only what is new after that. Safe to call every morning; a non-zero exit means the
# run or a hard check failed. Every target is a thin wrapper over the `sec-filings` command (src/sec_filings/cli.py).

# The job reads DATABASE_URL from the environment, then from .env. Only when there is neither does make supply the
# database in docker-compose.yml, and only then does it start Docker. make never reads .env: the job is its one reader.
# That database listens on localhost only, on this port; `DB_PORT=5433 make run` moves it, data included.
ifeq ($(origin DATABASE_URL)$(wildcard .env),undefined)
override DB_PORT := $(or $(DB_PORT),25432)
export DB_PORT
export DATABASE_URL := postgresql://sec:sec@localhost:$(DB_PORT)/sec_filings
START_DB := db
endif

# Set PYTHON to choose another interpreter.
PYTHON ?= $(shell command -v python3.12 || echo python3)
CLI := .venv/bin/sec-filings
PERIOD ?= FY

.DEFAULT_GOAL := help
.PHONY: help run backfill check report summary verify-labels test test-db setup db

help:  ## List the targets
	@awk -F':.*## ' '/^[a-z-]+:.*## / {printf "%-16s%s\n", $$1, $$2}' $(MAKEFILE_LIST)

run: setup $(START_DB)  ## THE ONE COMMAND: load, label and check; exit non-zero if a hard check fails
	@$(CLI) run

backfill: setup $(START_DB)  ## Run, and also re-read the history of filers with a coverage gap (FULL=1: of every filer)
	@$(CLI) backfill $(if $(FULL),--full)

check: setup $(START_DB)  ## Reprint the latest run's checks, no fetching; fails if no clean run today
	@$(CLI) check

report: setup $(START_DB)  ## One report: make report TICKER=AAPL YEAR=2009 PERIOD=FY
	@$(CLI) report $(TICKER) $(YEAR) $(PERIOD)

summary: setup $(START_DB)  ## One line per company
	@$(CLI) summary

verify-labels: setup $(START_DB)  ## Reload the SEC's labels for every filer; fails on a real disagreement
	@$(CLI) verify-labels

test: setup  ## Offline unit tests
	@.venv/bin/pytest -q

test-db: setup $(START_DB)  ## The replay tests against Postgres (each in its own throwaway schema)
	@TEST_DATABASE_URL="$${DATABASE_URL:?this target needs DATABASE_URL in the environment, .env is not enough}" .venv/bin/pytest -q tests/test_replay_db.py

setup: $(CLI)  ## Create the virtualenv and install; does nothing when already done

$(CLI): pyproject.toml
	@"$(PYTHON)" -c 'import sys; sys.exit(sys.version_info < (3, 12) and "Python 3.12+ is needed, %s is %s: set PYTHON to a newer one" % (sys.executable, sys.version.split()[0]))'
	"$(PYTHON)" -m venv --clear .venv
	.venv/bin/pip install --quiet -e ".[dev]"
	@touch $@

db:  ## Start the database in docker-compose.yml (run does it when there is no DATABASE_URL and no .env)
	@docker compose up -d --wait --quiet-pull
