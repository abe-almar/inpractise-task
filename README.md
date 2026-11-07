# SEC filings tracker

A small daily job that keeps a Postgres table of every annual and quarterly report twenty companies have filed with
the SEC, each labelled with the fiscal year and period it covers. Source: EDGAR's free submissions API, no key.

## Run it

You need Python 3.12+ and a Postgres database. The quickest local database is the one in `docker-compose.yml`.

```bash
docker compose up -d                                   # local Postgres 17 (skip if you have one)
python3.12 -m venv .venv && .venv/bin/pip install -e .
export DATABASE_URL=postgresql://sec:sec@localhost:5432/sec_filings
.venv/bin/sec-filings backfill                         # first time: every history page, read once per filer (--full re-reads)
```

The one command the scheduler calls every morning:

```bash
.venv/bin/sec-filings run
```

It fetches the ticker map and the recent filings of every CIK (one request each, a few seconds), validates each
page, upserts the filings, labels them, refreshes the SEC's own labels for filers with news, runs the morning checks,
prints a report and exits 0 only when the run and every hard check passed. Running it twice in a morning changes nothing. `DATABASE_URL` is the only required setting; `SEC_USER_AGENT`,
`SEC_RATE_LIMIT_PER_SEC`, `COMPANIES_FILE` and `OVERRIDES_FILE` are optional (see `.env.example`; a `.env` in the
working directory is read).

## Ask it something

```bash
.venv/bin/sec-filings report AAPL 2009 FY      # what do we have for Apple's 2009 annual report? one row
.venv/bin/sec-filings report WMT 2027 Q1       # a quarter (fiscal year = the calendar year in which the year ends)
.venv/bin/sec-filings summary                  # one line per company
.venv/bin/sec-filings check                    # the latest run's stored checks, no fetching, plus "did it run today"
.venv/bin/sec-filings verify-labels            # reload the SEC's own XBRL labels for every filer; exit 1 on a real disagreement
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

Or schedule the command itself (`sec-filings run`) and alert on a non-zero exit code. The morning check that tells a
wrong run from a crashed one is described in `docs/morning-check.md`.

## Tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q                                                            # offline unit tests
TEST_DATABASE_URL=postgresql://sec:sec@localhost:5432/sec_filings .venv/bin/pytest -q tests/test_replay_db.py  # idempotence, needs Postgres
```

## Where to read next

- `AGENTS.md`: the handover kit for a person or an agent who has to run, extend or fix this (adding companies,
  what to check before saying "done").
- `docs/decisions.md`: every decision with its alternatives and the check behind it.
- `docs/morning-check.md`, `docs/what-i-didnt-trust.md`, `docs/analyst-question.md`: the written answers.
- `docs/evidence/`: the data that backs them, including the run logs and review reports.
