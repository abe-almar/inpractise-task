# Decision log

Status values: `proposed` (made before implementation, open to objection), `decided`, `revised`. Each entry names the
alternatives and the check that backs it. Dates are the day the entry was written. Evidence tables live in
`docs/evidence/`.

## D1. Which forms count as "annual" and "quarterly" reports (proposed 2026-11-07)

- Annual: `10-K`, `10-K405` (the pre-2003 variant, 26 of them in our 20 companies), `10-KT` (transition period),
  `20-F` (foreign private issuers), `40-F` (Canadian issuers, none here but free to support). Their `/A` amendments are
  stored as separate rows linked to the original period, flagged `is_amendment`.
- Quarterly: `10-Q`, `10-QT`, and their `/A` amendments.
- Late-filing notices `NT 10-K`, `NT 10-Q`, `NT 20-F` are ingested as a third family (`late_notice`), because they come
  from the same feed at no cost and the analyst's question needs them.
- Excluded: `6-K`. SAP, SHEL, BABA, NVO and TM never file a 10-Q; their interim results travel as 6-K, "furnished" not
  "filed", mixed with hundreds of other 6-Ks (SHEL has 1,415) and not identifiable from metadata. The table therefore
  has no quarterly rows for those five and says so explicitly rather than guessing.
- Alternative rejected: 6-K rows whose primary document description contains "quarter" (unreliable, would silently
  miss or over-include).
- Check: form counts per company in `docs/evidence/edgar-probe-2026-11-07.md`.

## D2. A company is not a CIK (proposed 2026-11-07)

- `companies` (ticker, name) and `company_ciks` (cik, valid_from, valid_to, how we know) are separate. A company may
  have several CIKs over time.
- Why: `XOM` maps today to ExxonMobil Holdings Corp (CIK 2115436), a holding company created on 2026-07-01 (form 8-K12B)
  with two 10-Qs to its name; thirty years of 10-Ks sit under Exxon Mobil Corp (CIK 34088), which carries no ticker any
  more and still filed its own 10-Q on 2026-08-03. A ticker-only design returns nothing for "Exxon's 2015 annual report".
- The daily run resolves tickers through `company_tickers.json` and hard-fails the morning check when a ticker's CIK
  changes instead of switching silently; a human (or the next agent) then adds the new CIK with evidence.
- `LCID`'s CIK belonged to a SPAC (Churchill Capital Corp IV) before the 2021 merger; its fiscal-2020 10-K is kept
  (the registrant filed it) and labelled with a note. Same treatment for any pre-merger history.
- Check: the two Exxon submissions JSON files (both have a 10-Q for the period ended 2026-06-30).

## D3. How a filing gets its fiscal year and period (proposed 2026-11-07)

- Primary rule, deterministic and applicable to every filing since 1994: from `reportDate` (period end) and the
  company's fiscal year end (`fiscalYearEnd`, MMDD). Fiscal year = the calendar year in which that fiscal year ends.
  Annual forms: year of the period end. Quarterly forms: the fiscal year is the one whose year end comes next after the
  period end; the quarter number is the count of three-month steps since the previous year end, rounded. Tolerance of
  ten days absorbs 52/53-week calendars (AAPL, WMT, GME).
- Cross-check, not primary: the SEC's own `fy` / `fp` labels per accession in the XBRL companyfacts API
  (`/api/xbrl/companyfacts/CIK##########.json`). Verified on AAPL: all 45 10-K/10-Q in the recent page carry a label,
  none conflicting, and the labels agree with the rule (period ended 2025-12-27 = FY2026 Q1). Not available before the
  XBRL era (roughly 2009-2011 onwards), so it cannot be the primary source. Mismatches are stored and surfaced, never
  auto-overwritten.
- Missing year end: BABA's `fiscalYearEnd` is null in the submissions JSON. The job infers it from the company's own
  20-F period ends (31 March) and stores it as an override with the evidence; the morning check flags any company
  whose year end is null and has no override.
- Transition periods (10-KT/10-QT) are labelled by period end and flagged `is_transition`; a change of fiscal year end
  is recorded as a new validity row for the company.
- Alternative rejected: parsing the filing's cover page (DocumentFiscalYearFocus from the inline XBRL document) for every
  filing. It is the same data as companyfacts, costs one request per filing, and still does not exist before XBRL.
- Check: unit tests on real (reportDate, fiscalYearEnd) pairs for all seven year-end patterns in the set
  (Dec, Jan, Mar, Jun, Sep, 52/53-week, null), plus the companyfacts comparison on new filings.

## D4. Storage model (proposed 2026-11-07)

- `filings`: primary key `accession_number`; cik, form, family (`annual` / `quarterly` / `late_notice`), `is_amendment`,
  filing date, report date, acceptance time (UTC), primary document and its URL, fiscal year, fiscal period
  (`FY`, `Q1`..`Q3`; a fourth-quarter 10-Q does not exist), label source, XBRL flags, size, first and last run that saw it.
- `companies`, `company_ciks`, `fiscal_year_ends` (per company, with validity and evidence), `ticker_cik_map` history.
- `runs` (one row per execution: started, finished, status, requests made, rows inserted/updated, errors) and
  `checks` (one row per check per run: name, severity, pass/fail, detail) so the morning check reads from the database,
  not from logs.
- `source_snapshots`: per CIK per run, the SHA-256 of the recent submissions page and the page itself as JSONB, written
  only when the hash changes. Lets a later agent diff yesterday against today and explains post-acceptance deletions.
- View `reports`: one row per (company, fiscal year, period) with the chosen original filing and the list of
  amendments, so "what do we have for Apple's 2009 annual report" is one query with one answer.
- Alternative rejected: a single flat table keyed by ticker (breaks on D2) or an ORM (nothing here needs one).

## D5. Backfill once, incremental every morning, never delete (proposed 2026-11-07)

- `backfill` reads the recent page and every older page listed under `filings.files` (JPM alone has 71 pages,
  26,470 filings) once; `run` reads only `company_tickers.json` and the 20 recent pages (about 21 requests) and
  upserts by accession number. Both are idempotent, so running twice in one morning changes nothing.
- A filing that disappears from the feed is never deleted; it is marked `not_seen_since` and reported, because EDGAR
  does remove filings after acceptance (fair-access page, "post-acceptance corrections and deletions").
- The recent page holds about 1,000 filings; for JPM that is less than a year of history, which is still far more
  than one day of filings, so the daily run cannot miss a report unless it has not run for months. The morning check
  measures that gap.

## D6. Talking to EDGAR (proposed 2026-11-07)

- Declared `User-Agent` with a name and contact e-mail, `Accept-Encoding: gzip, deflate`, 30-second timeout, one request
  at a time, a token bucket at 5 requests per second (half of the published maximum of 10), retries with exponential
  backoff on 429, 5xx and timeouts, and a hard stop after five retries so a sick EDGAR fails the run instead of looping.
- No API key, no crawling, no index files: the submissions API is the only source for the job; companyfacts is read
  only by the cross-check command.
- Check: `https://www.sec.gov/os/accessing-edgar-data` as read on 2026-11-07 (10 requests/second, declared user agent).

## D7. Database and schema management (proposed 2026-11-07)

- Postgres 17 in a `docker-compose.yml` for local use; any Postgres 14+ works through `DATABASE_URL`.
- Schema as numbered SQL files applied idempotently at start-up and tracked in `schema_migrations`; psycopg 3 with
  plain SQL. No Alembic, no ORM: the schema is small and a fresh agent can read SQL.

## D8. Packaging and the one command (proposed 2026-11-07)

- Python 3.12, `pyproject.toml`, `pip install -e .`, console script `sec-filings` with sub-commands `run`, `backfill`,
  `check`, `verify-labels`, `add-company`. The README's one command is `sec-filings run` with `DATABASE_URL` set.
- Prefect shape without Prefect: `sec_filings.flow.daily_run(settings) -> RunResult` is a plain function with no side
  effects outside the database; the README shows the six lines that wrap it in a Prefect flow, and nothing imports
  Prefect.
- Exit code is the contract with the scheduler: 0 only when the run and every hard check passed.

## D9. The morning check (proposed 2026-11-07)

Every run ends by executing checks against the database and recording them in `checks`; `sec-filings check` reruns
them without fetching and prints a one-screen report. "Wrong" is defined as any of:

1. No successful run row for today, or the run made fewer requests than companies (partial fetch).
2. A ticker now resolves to a different CIK than yesterday (the Exxon case) — hard fail.
3. A company's filing count went down, or a previously seen filing is missing from the feed.
4. A filing with no fiscal label, or two originals for the same (company, family, year, period) outside a documented
   reorganisation overlap.
5. A company whose last annual or quarterly report is older than its cadence allows (10-K due 60 to 90 days after year
   end, 10-Q 40 to 45 days after quarter end, 20-F 120 days) — reported as overdue, which is also the analyst's "late".
6. A new filing whose rule-based label disagrees with the SEC's XBRL label.
7. New amendments or late-filing notices since the last run (informational, listed by name).
8. EDGAR answered with 429 or 5xx at any point, or the run took longer than a stated budget.

Hard checks fail the process (non-zero exit, so the scheduler alerts); soft checks print warnings. "It didn't crash"
is not among the checks.

## D10. Tests (proposed 2026-11-07)

- Unit tests for the labelling rule on real pairs taken from the probe, one per year-end pattern, plus the known traps.
- A replay test: a trimmed real submissions page committed as a fixture, loaded into a temporary Postgres twice;
  the second run must change zero rows.
- A cross-check test: labels from the rule versus companyfacts labels on the fixture.
- No test talks to EDGAR; the live path is exercised by the one command itself.

## D11. The analyst's question: how far the table can go (proposed 2026-11-07)

- "Late": computed as filing date minus period end compared with the statutory deadline for the filer's category
  (large accelerated 60 days for annual and 40 for quarterly, accelerated 75/40, non-accelerated 90/45, foreign
  private issuers 120 days for the 20-F), rolled to the next business day, plus the NT notices themselves, which are
  the filer's own admission. Confidence is limited because the submissions API gives only the current filer category,
  not the historical one, and the deadline grace granted by an NT notice (15 days annual, 5 days quarterly).
- "Corrected": an amendment (`10-K/A`, `20-F/A`) for the period. Confidence is limited because many amendments add
  Part III information or an exhibit rather than correct a figure; telling those apart needs the amendment's own text,
  which the table does not store. The answer will say which it can prove and what it would add (the amendment's
  stated purpose from its first page, the historical filer status from the cover page).

## D12. Time (proposed 2026-11-07)

- All timestamps in UTC; EDGAR dates stay as dates. The brief's 06:00 run is assumed to be European time (the brief
  does not say); by then the previous ET business day is complete (EDGAR accepts filings until 22:00 ET, 04:00 CET)
  and the submissions API reflects it. If the scheduler runs in US time, the check "no run today" still holds and the
  only consequence is that a same-day filing appears the next morning.

## D13. How the agents are used (proposed 2026-11-07)

- Claude Code (this session) orchestrates, researches and implements; its sub-agents take bounded parallel pieces.
- Hermes profile `takehome` (gpt-6.1-sol, own sub-agents) does independent data audits: recompute labels by a different
  route, spot-check filings against EDGAR pages, and later play the "fresh agent" that must add companies from the
  handover kit alone.
- Hermes profile `takehomereviewer` (gpt-6.1-sol, report-only) runs adversarial review passes on every deliverable;
  reports are committed under `docs/review-evidence/` with the findings and how each was closed.
- Session logs from all three are exported into `agent-logs/` at the end.

## D14. Repository layout (proposed 2026-11-07)

```
README.md            one command, the example query, how a scheduler calls it
AGENTS.md            the handover kit entry point for any agent (decisions, checks, how to add a company)
brief.md, companies.csv
pyproject.toml, docker-compose.yml, .env.example
src/sec_filings/     edgar client, labelling rule, loader, checks, cli, flow
sql/                 numbered schema files
tests/               unit, replay and cross-check tests with fixtures
docs/decisions.md    this file
docs/morning-check.md, docs/what-i-didnt-trust.md, docs/analyst-question.md
docs/evidence/       probe tables and query outputs that back the decisions
docs/review-evidence/, docs/agent-runs/
agent-logs/          exported session logs
```
