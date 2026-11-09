# SEC filings tracker

A small daily job that keeps a Postgres table of every annual and quarterly report twenty companies have filed with
the SEC, each labelled with the fiscal year and period it covers. Source: EDGAR's free submissions API, no key.

## Run it

You need `make`, Python 3.12+ and Docker (no Docker if you bring your own Postgres, see below). `make` uses
`python3.12` if it finds it and `python3` otherwise; `PYTHON=python3.13 make run` chooses another.

```bash
make run
```

That is the one command, from a fresh clone and every morning after. It starts the local Postgres, installs the job
into `.venv` (the exact versions in `constraints.txt`; `make lock` refreshes them), fetches the ticker map and the filings of every CIK, validates each page, upserts the filings, labels
them, refreshes the SEC's own labels for filers with news, runs the morning checks, prints a report and exits 0 only
when the run and every hard check passed. The first run reads every filer's whole history (about 140 requests, about
a minute), as does the first run after adding a company, for that company; other runs read one page per CIK (about 22
requests, a few seconds). Running it twice in a morning changes nothing. Every command's output starts by naming the
database it uses (`database: localhost:25432/sec_filings`; the JSON commands `report` and `summary` say it on stderr),
so a green run can never be mistaken for a run against another database.

The job reads its connection from `DATABASE_URL`, in the environment or in a `.env` file. With neither, `make` supplies
the database in `docker-compose.yml`, and that is the only case in which it starts Docker (`make db` starts it by hand).
That database listens on localhost only, on port 25432; when `make` is the one supplying it, `DB_PORT=5433 make run`
moves it, data included. `SEC_USER_AGENT`,
`SEC_RATE_LIMIT_PER_SEC`, `COMPANIES_FILE` and `OVERRIDES_FILE` are optional (see `.env.example`).

## Ask it something

```bash
make report TICKER=AAPL YEAR=2009 PERIOD=FY    # what do we have for Apple's 2009 annual report? one row
make report TICKER=WMT YEAR=2027 PERIOD=Q1     # a quarter (fiscal year = the calendar year in which the year ends)
make summary                                   # one line per company
make check                                     # the latest run's stored checks, no fetching, plus "did it run today" and
                                               # "did companies.csv or overrides.toml change since" (its checks used the old files)
make verify-labels                             # reload the SEC's own XBRL labels for every filer; fails on a real disagreement
make backfill                                  # re-read the history of filers with a coverage gap (FULL=1: of every filer)
make help                                      # all of the above
```

The same question in SQL, against the `reports` view (one row per company, family, fiscal year and period):

```sql
SELECT form, accession_number, filing_date, report_date, primary_document_url, amendments, sec_fiscal_year
FROM reports WHERE ticker = 'AAPL' AND fiscal_year = 2009 AND fiscal_period = 'FY';
```

## What is in the database

| table | what |
|---|---|
| `filings` | one row per tracked filing (key: accession number): form, dates, URL, fiscal label and how it was derived, co-registrant CIKs, corrections and notes |
| `reports` (view) | the original report per (ticker, family, year, period) plus its amendments and co-filed originals |
| `companies`, `company_ciks` | the ticker list and every CIK that belongs to a company, with validity and evidence |
| `filers` | what EDGAR says about each CIK (name, fiscal year end, filer category, former names) |
| `runs`, `checks` | one row per execution and one row per check per run: the morning check reads these |
| `sec_labels` | the SEC's XBRL fiscal labels per filing, for the cross-check |
| `source_snapshots` | each submissions page as fetched, kept only when it changed, for diffing |
| `ticker_cik_map` | what `company_tickers.json` said at every run |

Forms tracked: annual `10-K`, `10-K405`, `10-KT`, `10-KT405`, `10-KSB`, `20-F`, `40-F`; quarterly `10-Q`, `10-QT`,
`10-QSB`; their `/A` amendments; late-filing notices `NT 10-K`, `NT 10-K405`, `NT 10-Q`, `NT 20-F`. Five of the twenty
companies (SAP, SHEL, BABA, NVO, TM) are foreign private issuers: they file a `20-F` once a year and no quarterly
report at all, so they have no quarterly rows.

## Fiscal labels

`fiscal_year` is the calendar year of the nominal fiscal year end nearest the period end; quarters count from the
previous nominal year end. Apple's year ended 2009-09-26 is `2009 FY`; its quarter ended 2025-12-27 is `2026 Q1`;
Walmart's quarter ended 2026-04-30 is `2027 Q1`; a 52/53-week year ending 2027-01-02 against a 31 December year end
is `2026 FY`. Some issuers name years by their start (GameStop's year ended 2025-02-01 is "fiscal 2024" to GameStop):
they are listed in `overrides.toml` with evidence, and the SEC's own label is stored next to ours in
`sec_fiscal_year` / `sec_fiscal_period` for the filings that have XBRL (2009 onwards). When EDGAR's "period of
report" is wrong for a filing, `overrides.toml` carries the corrected date with the evidence read, and the label
follows it (`label_method = 'corrected'`). Decision D3 in `docs/decisions.md` explains the choice and the alternatives.

## Scheduling

The job is a plain function, `sec_filings.flow.daily_run(settings, mode)`, which returns a `RunResult` with the
counts and the check results. Nothing imports Prefect. To run it under Prefect, this is the whole wrapper:

```python
from prefect import flow
from sec_filings.flow import daily_run
from sec_filings.settings import Settings

@flow(name="sec-filings-daily")
def sec_filings_daily() -> None:
    result = daily_run(Settings.from_env(), mode="daily")
    if not result.ok:
        raise RuntimeError(f"run {result.run_id}: attention needed (see the checks table)")
```

Or schedule the command itself and alert on a non-zero exit code: `cd /path/to/repo && make run` (with `docker` on the
scheduler's `PATH` if it uses the compose database). The morning check that tells a wrong run from a crashed one is
described in `docs/morning-check.md`.

## Tests

```bash
make test       # offline unit tests; the Postgres replay tests are skipped
make test-db    # the replay tests (idempotence, outage recovery), each in its own throwaway schema
```

## How the code is organised

| module | does |
|---|---|
| `settings.py` | reads the environment (`DATABASE_URL`, user agent, rate, file paths) |
| `edgar.py` | the HTTP client: declared user agent, spacing, retries, URLs |
| `fiscal.py` | the pure labelling rule: form family, fiscal year and period from the period end and the year end |
| `db.py` | connection and the one-time application of `sql/001_schema.sql` |
| `loader.py` | EDGAR JSON to the table: companies and CIKs, one filer's pages, upsert, disappearances, coverage, labels, overrides |
| `checks.py` | the sixteen morning checks, one function each |
| `flow.py` | `daily_run`: the steps in order, the function a scheduler calls |
| `verify.py` | the SEC's own XBRL labels, loaded for the cross-check |
| `report.py`, `cli.py` | answers for humans and the `sec-filings` command, which the `Makefile` targets wrap |

## Where to read next

- `AGENTS.md`: the handover kit for a person or an agent who has to run, extend or fix this (adding companies,
  what to check before saying "done").
- `docs/decisions.md`: every decision with its alternatives and the check behind it.
- `docs/morning-check.md`, `docs/what-i-didnt-trust.md`, `docs/analyst-question.md`: the written answers.
- `docs/evidence/`: the data that backs them, including the run logs and review reports.
