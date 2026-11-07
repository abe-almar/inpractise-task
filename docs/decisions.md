# Decision log

Each entry states the rule as it stands, the alternatives that were rejected, the check that backs it, and a
**History** line when the data or a review changed it. Dates are 2026-11-07 unless stated; everything was built
that day. Evidence lives in `docs/evidence/`, review reports in `docs/review-evidence/`.

## D1. Which forms count as "annual" and "quarterly" reports

- Annual: `10-K`, `10-K405` (the pre-2003 variant, 26 of them in our 20 companies), `10-KT` and `10-KT405`
  (transition periods), `10-KSB` (small business, pre-2009), `20-F` (foreign private issuers), `40-F` (Canadian
  issuers, none here). Quarterly: `10-Q`, `10-QT`, `10-QSB`. Their `/A` amendments are separate rows linked to the
  original period and flagged `is_amendment`; an amendment can be exhibit-only or carry employee-plan statements
  filed under the issuer's CIK, and is still an amendment of that year's report (the analyst answer classifies them
  from their explanatory notes).
- Late-filing notices `NT 10-K`, `NT 10-K405`, `NT 10-Q`, `NT 20-F` are a third family (`late_notice`): same feed,
  no cost, and the analyst's question needs them.
- Excluded: `6-K`. SAP, SHEL, BABA, NVO and TM never file a 10-Q; their interim results are 6-Ks, "furnished" not
  "filed", mixed with hundreds of other 6-Ks (SHEL has 1,415) and not identifiable from metadata. The table has no
  quarterly rows for those five and says so.
- Rejected: 6-K rows whose document description contains "quarter" (unreliable either way).
- Check: form counts per company in `docs/evidence/edgar-probe-2026-11-07.md`.

## D2. A company is not a CIK

- `companies` (ticker) and `company_ciks` (cik, validity, how we know) are separate; names live on `filers`. A
  company may own several CIKs over time: `XOM` maps today to ExxonMobil Holdings Corp (CIK 2115436), a holding
  company created on 2026-07-01, while thirty years of 10-Ks sit under Exxon Mobil Corp (CIK 34088), which no
  longer carries a ticker. Predecessor CIKs live in `overrides.toml` `[[extra_ciks]]` with evidence (the fresh-agent
  test found Google's and Disney's the same way).
- A ticker whose CIK changes is noticed, never adopted silently: both CIKs keep being fetched and a hard check fails
  until a person records the old one. `LCID`'s CIK belonged to a SPAC shell until the 2021 merger; its shell-period
  filings are kept and carry a note (`[[filing_notes]]`).
- A filing listed by several registrants under one accession number (Exxon's Q2 2026 10-Q) is stored once, under
  the first registrant that listed it; the others are in `also_filed_by_ciks`, and the first registrant's metadata
  is kept. The `reports` view reaches a filing through every registrant.
- Check: the two Exxon submissions JSON files; `sec-filings report XOM 2026 Q2`.
- History: the probe note first said both CIKs had filed a Q2 2026 10-Q; the backfill showed one combined filing.
  Review pass 1 found the second registrant's page overwriting the first's file number and URL; fixed.

## D3. How a filing gets its fiscal year and period

- Rule (`fiscal.label`): from the period of report and the company's fiscal year end (MMDD). Fiscal year = the
  calendar year of the nominal year end nearest the period end (Apple's year ended 2009-09-26 is 2009; a 52/53-week
  year ending 2027-01-02 against 31 December is 2026, as the company itself names it). Quarters count from the
  previous nominal year end (Apple's quarter ended 2025-12-27 is 2026 Q1). Ten days of tolerance absorb 52/53-week
  calendars. One rule for everyone, never bent per company.
- When EDGAR's period of report is off the fiscal calendar and within 14 days of the filing date (an old metadata
  quirk that stores the filing date as the period, 19 filings from 1994-2015), the label comes from the last nominal
  period end at least 14 days before the filing date (`rule_filing_date`); for an amendment that inference can point
  at the wrong period, so each such amendment is a hard finding until a person reads it.
- When EDGAR's period of report is wrong for one filing, `[[filing_corrections]]` carries the cover's date with the
  evidence; EDGAR's value stays in `report_date`, the label follows the correction (`corrected`). See D15.
- Fiscal year end: EDGAR's `fiscalYearEnd`, or a `[[fiscal_year_ends]]` override (Alibaba's is null in EDGAR),
  optionally dated (`valid_from` / `valid_to` on the period end) when a company changed its year end.
- Cross-check, never the source: the SEC's own `fy` / `fp` per accession from the XBRL companyfacts API, stored in
  `sec_labels` and shown beside ours in the `reports` view. It does not exist before the XBRL era (about 2009).
  Issuers that name the year by its start are an explicit list with evidence (`[[naming_conventions]]`: GameStop
  always; Walmart up to 2014-03-21; Toyota up to 2017-06-23); any other disagreement is a real finding.
- Rejected: parsing every filing's cover page (same data as companyfacts, one request per filing, still nothing
  before XBRL); a per-company rule (the SEC labels show two companies switching convention over time).
- Check: `tests/test_fiscal.py` on real dates for every year-end pattern in the set; `labels_match_sec` on every run.
- History: the first backfill produced four duplicate (company, year, period) keys, all from the filing-date quirk;
  the first wording "year in which the fiscal year ends" contradicted the implementation (review pass 1); "any
  one-year difference is a convention" would have excused an off-by-one bug in our own rule (pass 1); the Apple
  1997 10-Q/A inference pointed at Q3 where the cover says Q2 (fixed by a correction); month-end year ends drifted
  a day in quarter arithmetic (Microsoft's 30 December).

## D4. Storage model

- `filings`: one row per tracked filing, keyed by accession number, with form, family, dates, URL, the fiscal label
  and how it was derived, co-registrants, corrections and notes. Never deleted (D5).
- `companies`, `company_ciks`, `ticker_cik_map` (what the SEC's ticker file said at each run), `filers` (what EDGAR
  says about a CIK, plus the job's per-filer bookkeeping: recent window, backfill marker, coverage watermark and gap,
  SEC-label attempt time), `sec_labels`, `runs`, `checks` (one row per check per run), `source_snapshots` (each page
  as fetched, kept when its content changed, with the last run that saw the same content).
- View `reports`: one row per (company, family, fiscal year, period) with the chosen original, its amendments and
  any co-filed originals, so the one-row question is one query.
- The schema is a single commented file, `src/sec_filings/sql/001_schema.sql`, applied once and recorded in
  `schema_migrations`; later changes are new numbered files.
- Rejected: a flat table keyed by ticker (breaks on D2); an ORM (nothing here needs one); a fiscal-year-end table
  (overrides.toml carries the dated entries).
- History: the schema grew through seven migrations during the review passes (co-registrants, bookkeeping,
  corrections, coverage, refresh queue) and was collapsed into one file in the final simplification pass; the git
  history keeps the sequence.

## D5. Backfill once, incremental every morning, never delete

- `backfill` reads the recent page and every history page listed under `filings.files` (JPM alone has 71 pages)
  once per filer, again for a filer with a coverage gap on record, and for everyone with `--full`. `run` reads the
  ticker file and one recent page per CIK (about 22 requests) and upserts by accession number. Both are idempotent.
- A page counts as fetched only after validation (right CIK, non-empty and consistent recent block); a failing page
  is recorded and fails `run_complete` without aborting the other filers.
- A filing that disappears from the pages is never deleted: it gets `not_seen_since`, stays a hard finding until it
  reappears or a person accepts it (EDGAR does remove filings after acceptance). The comparison window is the page
  fetched plus where the previous fetch started, so the oldest filing on a page cannot vanish unnoticed.
- Coverage is a per-filer watermark (`coverage_through`): a history read sets it to today; a recent page that
  overlaps it advances it; a page that starts after it records a gap that only a history read clears. So a run that
  merely reached its checks cannot bridge an outage (`history_gap`).
- Check: `docs/evidence/outage-recovery-2026-11-07.log` (gap detected, persisted, repaired, cleared, live);
  `tests/test_replay_db.py`.
- History: review passes 2 to 4 each broke the previous version of this rule: a run-level "last good run" baseline
  faded the alarm, then blocked recovery, then its initialisation cleared an inherited gap. The watermark replaced it.

## D6. Talking to EDGAR

- Declared `User-Agent` with a name and contact e-mail, `Accept-Encoding: gzip, deflate`, 30-second timeout, one
  request at a time with 0.2 s between requests (5 per second, half the published maximum; retries respect the same
  spacing), exponential backoff on 408, 429 and every 5xx, a hard stop after five retries. No API key, no crawling,
  no index files: the submissions API is the source, companyfacts the cross-check.
- Check: `https://www.sec.gov/os/accessing-edgar-data` as read on 2026-11-07; `tests/test_edgar_client.py`.

## D7. Database and schema management

- Postgres 17 in `docker-compose.yml` for local use; any Postgres 14+ through `DATABASE_URL`. psycopg 3 with plain
  SQL; the schema file is read by people as much as by the database.

## D8. Packaging and the one command

- Python 3.12, `pyproject.toml`, `pip install -e .`, console script `sec-filings` with `run`, `backfill [--full]`,
  `check`, `verify-labels`, `report`, `summary`. The README's one command is `sec-filings run` with `DATABASE_URL`
  set; `.env` in the working directory is read. Adding a company is a line in `companies.csv` plus `backfill`.
- Prefect shape without Prefect: `sec_filings.flow.daily_run(settings, mode)` is a plain function; the README shows
  the six-line wrapper. The exit code is the contract with the scheduler: 0 only when the run and every hard check
  passed.

## D9. The morning check

- Sixteen named checks, evaluated at the end of every run and stored in `checks`; hard checks fail the process, soft
  ones warn, info never fails. `sec-filings check` re-reads the latest run's stored results and adds "did a clean run
  start today". Accepting a finding is an explicit `[[accepted_findings]]` entry with what was looked at; the whole
  item list is evaluated against the acceptances. The list and what each check catches: `docs/morning-check.md`.
- History: pass 1 of the review found the disappearance alarm firing once and then going quiet, an empty page
  counting as fetched, acceptance evaluated on a truncated list, and the 20-F deadline in days; passes 2 to 4 found
  the coverage-baseline defects (D5) and a refresh queue that could starve a filer. All fixed; pass 5 passed.

## D10. Tests

- Unit tests of the labelling rule on real dates for every year-end pattern; parser, overrides, client, deadline and
  check-helper tests with fakes; a database replay (ten runs on trimmed real pages) that proves idempotence,
  the disappearance, correction, coverage and refresh behaviours. No test talks to EDGAR. 292 tests.

## D11. The analyst's question: how far the table can go

- "Late" = filing date past the statutory deadline for the filer's category (large accelerated 60 days annual, 40
  quarterly; accelerated 75/40; non-accelerated 90/45; foreign private issuers four calendar months for the 20-F),
  rolled to the next business day, plus the NT notices. The filer's category at filing time is not in the table
  (EDGAR gives today's), so candidates were checked against their cover pages. "Corrected" = an amendment, classified
  from its explanatory note, because most amendments add Part III or exhibits rather than correct anything.
- Rejected: trusting today's category (nine candidates, one real late filer).
- Answer and evidence: `docs/analyst-question.md`, `docs/evidence/late-filers-cover-check-2026-11-07.md`,
  `docs/evidence/amendments-purpose.md`.
- History: the foreign-private-issuer class was first taken from EDGAR's `category`, which calls Shell "Large
  accelerated filer"; the form of the latest annual report decides now.

## D12. Time

- All timestamps UTC; EDGAR dates stay dates. The brief's 06:00 run is assumed European; by then the previous US
  business day is complete (EDGAR accepts filings until 22:00 ET). If the scheduler runs in US time, "ran today"
  still holds and a same-day filing shows the next morning.

## D13. How the agents were used

- Claude Code orchestrated, researched and implemented, with a sub-agent for the tests. The Hermes profile
  `takehome` (gpt-6.1-sol) audited the table through a different EDGAR route (Atom feeds; 183 filings, MATCH) and,
  with no persona injected, played the fresh agent that must add five companies from the kit alone. The Hermes
  profile `takehomereviewer` ran five adversarial passes (one full, four deltas): FAIL, FAIL, FAIL, FAIL, PASS. Session
  exports are under `agent-logs/`; the setup is described in `docs/agent-setup.md`.

## D14. Repository layout

```
README.md            one command, the example query, how a scheduler calls it, where each module lives
AGENTS.md            the handover kit for any agent (decisions, checks, how to add a company, how to say "done")
brief.md, companies.csv, overrides.toml, pyproject.toml, docker-compose.yml, .env.example
src/sec_filings/     settings, edgar (client), fiscal (rule), db (schema), loader (EDGAR -> table), checks, flow, verify, report, cli
src/sec_filings/sql/ 001_schema.sql
tests/               unit, replay and cross-check tests with trimmed real fixtures
docs/                this log, morning-check, what-i-didnt-trust, analyst-question, agent-setup, agent-personas
docs/evidence/       probes, run logs, cover-page checks, tools; docs/review-evidence/ the five review reports;
                     docs/agent-runs/ the audit and the fresh-agent test
agent-logs/          exported session transcripts
```

## D15. Corrections of EDGAR's own metadata are per filing, evidenced, and never silent

- A wrong `reportDate` (Disney's 10-Q filed 2009-05-05: EDGAR says 2008-03-28, the cover says March 28, 2009;
  Apple's 1997 10-Q/A) cannot be fixed by the year end or by the rule. The fix is a `[[filing_corrections]]` entry
  with the accession, the corrected period end, a note and the evidence. The loader keeps EDGAR's value, stores the
  correction beside it, labels from it and marks the row `corrected`; removing the entry removes the correction.
- Rejected: editing rows by hand (invisible, lost on relabel); accepting the duplicate-key finding (the `reports`
  view would still miss the real period); bending the rule per company.
- Check: `sec-filings report AAPL 1997 Q2`; the replay test.
- History: found by the fresh-agent test, which stopped on the Disney filing because the kit had no way to express
  the correction.
