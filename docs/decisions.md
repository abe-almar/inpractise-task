# Decision log

Status values: `proposed` (made before implementation, open to objection), `decided` (held up in the build), `revised` (changed by what the data showed; the entry says what). Each entry names the
alternatives and the check that backs it. Dates are the day the entry was written. Evidence tables live in
`docs/evidence/`.

## D1. Which forms count as "annual" and "quarterly" reports (proposed 2026-11-07)

- Annual: `10-K`, `10-K405` (the pre-2003 variant, 26 of them in our 20 companies), `10-KT` and `10-KT405`
  (transition periods), `10-KSB` (small business, pre-2009), `20-F` (foreign private issuers), `40-F` (Canadian
  issuers, none here but free to support). Their `/A` amendments are stored as separate rows linked to the original
  period, flagged `is_amendment`. An amendment can be exhibit-only or carry employee-plan statements filed under the
  issuer's CIK (Chemical Banking's 1994 and 1995 10-K/As, four Disney ones found by the fresh agent): it is still an
  amendment of that year's report, and the analyst answer classifies it from its explanatory note.
- Quarterly: `10-Q`, `10-QT`, `10-QSB`, and their `/A` amendments.
- Late-filing notices `NT 10-K`, `NT 10-Q`, `NT 20-F` are ingested as a third family (`late_notice`), because they come
  from the same feed at no cost and the analyst's question needs them.
- Excluded: `6-K`. SAP, SHEL, BABA, NVO and TM never file a 10-Q; their interim results travel as 6-K, "furnished" not
  "filed", mixed with hundreds of other 6-Ks (SHEL has 1,415) and not identifiable from metadata. The table therefore
  has no quarterly rows for those five and says so explicitly rather than guessing.
- Alternative rejected: 6-K rows whose primary document description contains "quarter" (unreliable, would silently
  miss or over-include).
- Check: form counts per company in `docs/evidence/edgar-probe-2026-11-07.md`.

## D2. A company is not a CIK (proposed 2026-11-07; revised the same day)

- `companies` (ticker, name) and `company_ciks` (cik, valid_from, valid_to, how we know) are separate. A company may
  have several CIKs over time.
- Why: `XOM` maps today to ExxonMobil Holdings Corp (CIK 2115436), a holding company created on 2026-07-01 (form 8-K12B)
  with two 10-Qs to its name; thirty years of 10-Ks sit under Exxon Mobil Corp (CIK 34088), which carries no ticker any
  more and still filed its own 10-Q on 2026-08-03. A ticker-only design returns nothing for "Exxon's 2015 annual report".
- The daily run resolves tickers through `company_tickers.json` and hard-fails the morning check when a ticker's CIK
  changes instead of switching silently; a human (or the next agent) then adds the new CIK with evidence.
- `LCID`'s CIK belonged to a SPAC (Churchill Capital Corp IV) before the 2021 merger; its fiscal-2020 10-K is kept
  (the registrant filed it) and labelled with a note. Same treatment for any pre-merger history.
- Check: the two Exxon submissions JSON files.
- Revised after the first backfill: the Q2 2026 10-Q (accession 0000034088-26-000093) is ONE combined filing listed
  under both CIKs, not two filings as the probe note first said. A filing can have several registrants under one
  accession number, so `filings` keeps the first CIK that listed it and records the others in `also_filed_by_ciks`
  (migration `002_co_registrants.sql`). The `reports` view therefore shows it once, and reaches it through either
  registrant. The first registrant's metadata is kept: a second registrant's page only marks the filing as seen
  (review pass 1 found the old CIK's page overwriting the file number and document URL).

## D3. How a filing gets its fiscal year and period (proposed 2026-11-07; revised the same day)

- Primary rule, deterministic and applicable to every filing since 1994: from `reportDate` (period end) and the
  company's fiscal year end (`fiscalYearEnd`, MMDD). Fiscal year = the calendar year of the nominal year end nearest
  the period end (so a 52/53-week year ending 2 January belongs to the previous calendar year, as the company names
  it; review pass 1 caught the earlier wording "year in which it ends", which contradicted the implementation).
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
  is a dated `[[fiscal_year_ends]]` entry in `overrides.toml` (`valid_from` / `valid_to` on the period end), chosen
  per filing at relabel time. Review pass 1 found this promised but not built; it is built now, unused by the 20.
- Per-filing corrections (added after the fresh-agent test, D15): when EDGAR's `reportDate` is wrong for one filing
  (Disney's 10-Q of 2009-05-05 says 2008 in EDGAR and 2009 on the cover; Apple's 1997 10-Q/A was inferred as Q3 but
  amends Q2), `[[filing_corrections]]` carries the cover's date with the evidence; EDGAR's value stays in
  `report_date`, the label follows the correction (`label_method = 'corrected'`).
- Issuer naming conventions are an explicit list (`[[naming_conventions]]`: GameStop; Walmart up to 2014-03-21;
  Toyota up to 2017-06-23), not a heuristic: review pass 1 showed that "any one-year difference" would have excused
  an off-by-one bug in our own rule for every company.
- Alternative rejected: parsing the filing's cover page (DocumentFiscalYearFocus from the inline XBRL document) for every
  filing. It is the same data as companyfacts, costs one request per filing, and still does not exist before XBRL.
- Check: unit tests on real (reportDate, fiscalYearEnd) pairs for all seven year-end patterns in the set
  (Dec, Jan, Mar, Jun, Sep, 52/53-week, null), plus the companyfacts comparison on new filings.
- Revised after the first backfill (two things the data showed):
  1. For 20 filings, mostly 1994-2015, EDGAR's `reportDate` is the filing date itself (gap 0 to 5 days), which put
     a GE 10-K filed 2015-02-27 in fiscal 2015 next to the real fiscal 2015 report and created four duplicate
     (company, year, period) keys. When the period of report is within 14 days of the filing date and off the
     calendar, the label now comes from the last nominal period end at least 14 days before the filing date
     (`label_method = 'rule_filing_date'`); all four duplicates disappeared and the SEC labels agree with the result.
  2. The SEC's own XBRL labels disagree with the convention for 101 filings in a clean pattern: GameStop (all 73),
     Walmart (20, only up to the 10-K filed 2014-03-21) and Toyota (8, up to the 20-F filed 2017-06-23) name the
     fiscal year by the calendar year in which it STARTS, and two of them changed convention over time. That is why
     the rule stays one deterministic convention and the SEC label is stored beside it (`sec_labels`, and the
     `sec_fiscal_year` / `sec_fiscal_period` columns of the `reports` view). Four further disagreements are SEC-side
     errors verified on the filings' cover pages (listed in `overrides.toml`).
  3. Nominal quarter ends are month ends when the year end is a month end (30 June + 6 months = 31 December, not
     30 December); found when the overdue check printed Microsoft's next quarter end as 30 December.

## D4. Storage model (proposed 2026-11-07)

- `filings`: primary key `accession_number`; cik, form, family (`annual` / `quarterly` / `late_notice`), `is_amendment`,
  filing date, report date, acceptance time (UTC), primary document and its URL, fiscal year, fiscal period
  (`FY`, `Q1`..`Q3`; a fourth-quarter 10-Q does not exist), label source, XBRL flags, size, first and last run that saw it.
- `companies` (ticker, active), `company_ciks`, `ticker_cik_map` history; filer names live on `filers`. Fiscal year
  ends with validity live in `overrides.toml`, not in a table (revised: the table promised here was never needed).
- `runs` (one row per execution: started, finished, status, requests made, rows inserted/updated, errors) and
  `checks` (one row per check per run: name, severity, pass/fail, detail) so the morning check reads from the database,
  not from logs.
- `source_snapshots`: per CIK and page, the SHA-256 of the response and the full parsed page as JSONB, written when
  the hash changes, with the last run that saw the same content (so A, B, A sequences are visible). Lets a later agent
  diff yesterday against today and explains post-acceptance deletions. Revised after review pass 1, which found a
  trimmed object stored under a hash of the full response.
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
  than one day of filings, so the daily run cannot miss a report unless it has not run for months. The `history_gap`
  check measures exactly that: if the previous successful run is older than the oldest filing on today's recent page,
  the run fails and asks for `backfill --full`. History pages are read once per filer (`backfill_completed_at`), so
  adding a company does not re-read everyone else's history (review pass 1 and the fresh agent both found that it did).
- A page is validated before it counts as fetched: it must be for the requested CIK, with a non-empty, consistent
  recent block; otherwise the filer counts as failed and `run_complete` fails (review pass 1: an empty page passed).

## D6. Talking to EDGAR (proposed 2026-11-07)

- Declared `User-Agent` with a name and contact e-mail, `Accept-Encoding: gzip, deflate`, 30-second timeout, one request
  at a time, a minimum spacing of 0.2 s (5 requests per second, half of the published maximum of 10; retries respect
  the same spacing), retries with exponential backoff on 408, 429 and every 5xx, and a hard stop after five retries so
  a sick EDGAR fails the run instead of looping.
- No API key, no crawling, no index files: the submissions API is the only source for the job; companyfacts is read
  only by the cross-check command.
- Check: `https://www.sec.gov/os/accessing-edgar-data` as read on 2026-11-07 (10 requests/second, declared user agent).

## D7. Database and schema management (proposed 2026-11-07)

- Postgres 17 in a `docker-compose.yml` for local use; any Postgres 14+ works through `DATABASE_URL`.
- Schema as numbered SQL files applied idempotently at start-up and tracked in `schema_migrations`; psycopg 3 with
  plain SQL. No Alembic, no ORM: the schema is small and a fresh agent can read SQL. Decided; `002_co_registrants.sql`
  was the first real migration (D2).

## D8. Packaging and the one command (proposed 2026-11-07)

- Python 3.12, `pyproject.toml`, `pip install -e .`, console script `sec-filings` with sub-commands `run`,
  `backfill [--full]`, `check`, `verify-labels`, `report`, `summary`. Adding a company is a line in `companies.csv`
  plus `backfill`, not a command (the `add-company` promised here was dropped). The README's one command is
  `sec-filings run` with `DATABASE_URL` set.
- Prefect shape without Prefect: `sec_filings.flow.daily_run(settings) -> RunResult` is a plain function with no side
  effects outside the database; the README shows the six lines that wrap it in a Prefect flow, and nothing imports
  Prefect.
- Exit code is the contract with the scheduler: 0 only when the run and every hard check passed.

## D9. The morning check (proposed 2026-11-07; decided, see docs/morning-check.md for the final list)

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

## D11. The analyst's question: how far the table can go (proposed 2026-11-07; revised, see docs/analyst-question.md)

- "Late": computed as filing date minus period end compared with the statutory deadline for the filer's category
  (large accelerated 60 days for annual and 40 for quarterly, accelerated 75/40, non-accelerated 90/45, foreign
  private issuers 120 days for the 20-F), rolled to the next business day, plus the NT notices themselves, which are
  the filer's own admission. Revised: the foreign-private-issuer class is decided by the form of the latest annual
  report (20-F/40-F), not by EDGAR's `category`, which calls Shell and Toyota "Large accelerated filer" although their
  20-F deadline is four months regardless. Confidence is limited because the submissions API gives only the current filer category,
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
  reports are committed under `docs/review-evidence/` with the findings and how each was closed. Pass 1 (2026-11-07,
  verdict FAIL, nine major findings) drove migrations 003 and 004 and the current check list.
- The fresh-agent test (2026-11-07, no persona, five new companies in an isolated worktree and database) found the
  Google and Disney predecessor CIKs on its own, stopped on a Disney filing with a wrong EDGAR period because the kit
  had no per-filing correction, and proposed nine kit changes; all are in the kit or the code now
  (`docs/agent-runs/fresh-agent-2026-11-07/`).
- Session logs from all three are exported into `agent-logs/` at the end.

## D14. Repository layout (proposed 2026-11-07)

```
README.md            one command, the example query, how a scheduler calls it
AGENTS.md            the handover kit entry point for any agent (decisions, checks, how to add a company)
brief.md, companies.csv
pyproject.toml, docker-compose.yml, .env.example
src/sec_filings/     edgar client, labelling rule, loader, checks, cli, flow
src/sec_filings/sql/ numbered schema files (inside the package so `pip install` ships them)
tests/               unit, replay and cross-check tests with fixtures
docs/decisions.md    this file
docs/morning-check.md, docs/what-i-didnt-trust.md, docs/analyst-question.md
docs/evidence/       probe tables and query outputs that back the decisions
docs/review-evidence/, docs/agent-runs/
agent-logs/          exported session logs
```

## D15. Corrections of EDGAR's own metadata are per filing, evidenced, and never silent (decided 2026-11-07)

- A wrong `reportDate` (Disney's 10-Q filed 2009-05-05: EDGAR says 2008-03-28, the cover says March 28, 2009) or
  a missing one cannot be fixed by the fiscal year end or by the rule. The fix is an entry under
  `[[filing_corrections]]` with the accession, the corrected period end, a note and the evidence (what was read,
  where, when). The loader keeps EDGAR's value in `report_date`, stores the correction beside it, labels from it
  and marks the row `corrected`. Removing the entry removes the correction on the next run.
- Alternatives rejected: editing rows by hand (invisible, lost on relabel); accepting the duplicate-key finding
  (silences the check but the `reports` view would still miss the real period); bending the rule per company.
- Check: `sec-filings report AAPL 1997 Q2` shows the 10-Q with its amendment after the correction; the replay test
  covers the relabel.
