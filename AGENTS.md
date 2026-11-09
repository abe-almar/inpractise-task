# Handover kit: running, extending and fixing the SEC filings job

You are an agent (or a person) who has to keep this job alive without the author. Read this file, then
`README.md`, then `docs/decisions.md`. Everything else is reachable from those three. Work from the repository root;
every command below assumes it. This kit was tested by handing it to a fresh agent with five new companies
(`docs/agent-runs/fresh-agent-2026-11-07/`); what it stumbled on is fixed or stated here.

## 1. What this is, in five lines

A daily job keeps a Postgres table of every annual (10-K family, 20-F, 40-F) and quarterly (10-Q family) report
that the companies in `companies.csv` filed with the SEC, plus their amendments and late-filing notices, each
labelled with the fiscal year and period it covers. Source: EDGAR's submissions API. The one command is
`make run`; it exits 0 only when the run completed and every hard check passed. The `reports` view answers
"what do we have for Apple's 2009 annual report" with one row.

## 2. Set up and prove it works (ten minutes)

```bash
make test                                      # installs the job; expect: all passed, the DB tests skipped (see 7)
make run                                       # starts Postgres; first load, reads every filer's whole history
make run                                       # second run: "new=0", RESULT: OK
make report TICKER=AAPL YEAR=2009 PERIOD=FY    # one row, form 10-K, filed 2009-10-27, 1 amendment
make verify-labels                             # loads the SEC's XBRL labels; exit 0 unless a REAL disagreement
```

`make help` lists every target; each is a thin wrapper over `.venv/bin/sec-filings`. Dependencies are pinned in
`constraints.txt`: change them with `make lock` (never by hand), then run section 7. `make docker-run` runs the same
job in a container, for schedulers that run images (README, *Running it in a container*); nothing in this kit needs it. With no `DATABASE_URL` in the
environment and no `.env` file, `make` uses the Postgres in `docker-compose.yml` and starts it, on localhost only, port
25432 (`DB_PORT=5433 make run` moves it, data included); otherwise the job uses what you set, and `make db` starts the
compose one by hand. If `.venv` is broken (the repository moved, say), delete it and the next `make` rebuilds it. If
`run` ends with `RESULT: ATTENTION NEEDED`, read the `[FAIL]` lines; section 5 says what each means.
Counts in this kit (20 companies, 21 CIKs, 22 requests) describe the original list; yours follow `companies.csv`
and the CIKs in `overrides.toml`.

## 3. Decisions you must not undo without re-reading their entry

The full log with alternatives and evidence is `docs/decisions.md`. The ones that bite:

- **D1 forms.** Foreign private issuers (SAP, SHEL, BABA, NVO, TM, and any you add that file 20-F) never file a
  10-Q; their interim results are 6-Ks, which are deliberately not tracked. Do not "fix" their missing quarterly
  rows. Amendments include exhibit-only ones and employee-plan statements filed as 10-K/A: they are amendments of
  that year's report, not corrections, and the analyst question treats them as such.
- **D2 company is not CIK.** A company can own several CIKs over time (`company_ciks`): Exxon moved to a holding
  company CIK in 2026, Google to Alphabet's in 2015, Disney twice. Predecessor CIKs live in `overrides.toml`
  `[[extra_ciks]]` with evidence. A filing listed by several registrants is stored once; the other CIKs are in
  `also_filed_by_ciks`, and the first registrant's metadata is kept.
- **D3 labels.** `fiscal_year` = calendar year of the nominal fiscal year end nearest the period end; quarters
  count from the previous nominal year end. Issuers that name years by their start (GameStop; Walmart and Toyota in
  older years) are listed under `[[naming_conventions]]` with evidence; the SEC's own label is kept beside ours.
  A wrong EDGAR "period of report" is corrected per filing under `[[filing_corrections]]` with evidence, never by
  editing rows or bending the rule. A fiscal year end that changed over time is a dated `[[fiscal_year_ends]]` entry.
- **D5 never delete.** A filing that disappears from EDGAR gets `not_seen_since`, a hard check fails every morning
  until it reappears or a person accepts it. A filer's history is read the first time it is seen, on any run;
  `make backfill` re-reads it for a filer with a coverage gap on record, `make backfill FULL=1` for everyone.
- **D6 fair access.** User agent with a contact address, 5 requests per second, no crawling. Do not raise the
  rate; the daily run is about one request per CIK plus one for the ticker map.
- **D9 checks.** Hard checks fail the process; soft ones warn. Accepting a finding is an explicit entry in
  `overrides.toml` with what was looked at. Nothing is auto-accepted; not even a naming convention.

## 4. Adding companies (the quarterly request)

1. Append the tickers to `companies.csv` (one per row, header `ticker`). Tickers are matched case-insensitively
   against `https://www.sec.gov/files/company_tickers.json`.
2. Run `make run`. New CIKs get their full history; every other filer is refreshed from its recent page only.
3. Look for predecessor CIKs before trusting the counts. Signs: the first filing is recent while the company is
   old; `formerNames` in the submissions JSON; an `8-K12B`, `8-K12G3`, `8-B12B` or `8-B12G` among the first
   filings (a successor-registrant notice); a gap of years between the CIK's first 10-K and the company's history.
   Follow the chain recursively (Disney needed two predecessors). Add each under `[[extra_ciks]]` with `valid_to`,
   a note and the evidence, then `make run` again: the new CIKs' history is read on that run.
4. Read the checks. Expected failures for a new company, and what to do:
   - `tickers_resolved`: the ticker is not in EDGAR's file (delisted, wrong ticker, foreign line). Find the CIK on
     https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany and add it under `[[extra_ciks]]` with
     `current = true` and the evidence; the next run resolves the ticker through the override and says so.
   - `fiscal_year_end_known`: EDGAR has no `fiscalYearEnd`. Open the latest 10-K or 20-F, read the period of
     report, add `[[fiscal_year_ends]]` with `mmdd` and the evidence.
   - `labels_unique`: two originals for one (year, period). Open both cover pages. Usually EDGAR's "period of
     report" is wrong for one of them (Disney's 10-Q of 2009-05-05 says 2008 in EDGAR and 2009 on the cover):
     add `[[filing_corrections]]` with the cover's date and the evidence. Only when both filings genuinely cover
     the same period (a transition report, a re-filed original) record it under `[[accepted_findings]]`.
   - `labels_inferred_for_amendments`: an old amendment whose EDGAR period is its filing date was labelled by
     inference. Open the document (if `primary_document_url` is 404 on an old filing, open the filing index
     `.../<accession>-index.htm` and use the complete submission text it links). If the inferred label is right,
     accept it with what you read; if not, add a `[[filing_corrections]]` entry with the period the cover states.
5. Run `make verify-labels`, then `make run`. A `labels_match_sec` item is a
   real disagreement until proven otherwise: open the filing's cover page. If the issuer names its fiscal year by
   the start year (the cover says "fiscal 2010" for a year ended January 2011), add `[[naming_conventions]]` with
   `sec_year_offset = -1`, optionally `until` (the last filing date it applied to), and the evidence; a one-year
   difference is never a convention until it is listed. If the SEC label is simply wrong, record it under
   `[[accepted_findings]]` with what the cover says.
6. Spot-check by hand: pick one fiscal year of the new company, run `make report TICKER=X YEAR=Y`, open the
   document, and confirm the cover's "for the fiscal year ended" matches the label and `report_date`.
7. Add a test case to `tests/test_fiscal.py` if the year-end pattern is new (section 6).
8. Copy the run output into `docs/evidence/` with the date, add a dated entry to `docs/decisions.md` for any
   decision taken, then run the completion checks in section 7.

Taking a company off the list marks it inactive and keeps its filings (D5). Known limitation: `labels_match_sec` still
reads an inactive company's filings, so clear or accept its open findings before removing it, or the check stays red.

## 5. When the morning check fails

| failing check | first action |
|---|---|
| `run_complete` | one or more filer pages failed validation or fetching; the items say which and why (empty page, wrong CIK, HTTP error). Rerun `make run` (idempotent); if EDGAR is down the retry log shows 429/5xx |
| `tickers_resolved` | section 4, step 4 |
| `cik_changes_acknowledged` | the ticker moved to a new CIK. Compare both CIKs' submissions JSON (`name`, `formerNames`, first filing). Add the old CIK under `[[extra_ciks]]` with `valid_to` and the evidence. Both CIKs keep being fetched meanwhile; nothing is lost |
| `fiscal_year_end_known` | section 4, step 4 |
| `history_gap` | a filer has a coverage gap on record (its recent page started after the filer's coverage watermark, so filings may have passed through unseen): run `make backfill`, which re-reads the history of exactly the filers with a gap (`FULL=1` re-reads everyone); the gap clears only when the history is read. `docs/evidence/outage-recovery-2026-11-07.log` shows the whole sequence |
| `sec_labels_refreshed` | the SEC-label refresh failed for a filer (the items say why); it is retried on the next run, so rerun and look only if it persists |
| `no_filings_lost` | EDGAR stopped listing a filing. Open its index URL. If EDGAR removed it (post-acceptance deletion), accept it with the reason; if it reappears the flag clears itself |
| `labels_complete` | a report has no usable label: `no_report_date` (EDGAR gave none; add a `[[filing_corrections]]` entry from the cover) or `no_fye` (add the year end). Never edit rows by hand |
| `labels_unique` | section 4, step 4 |
| `labels_inferred_for_amendments` | section 4, step 4 |
| `labels_match_sec` | section 4, step 5; an item of kind `no_sec_label` is an XBRL filing older than three weeks with no companyfacts facts (some amendments have none): check the companyfacts API for the accession and accept it with that reason |
| `labels_unique_across_ciks` (soft) | two CIKs of one company both filed an original for the same period (reorganisation year). The view shows the current CIK's and lists the other; nothing to fix unless one is mislabelled |
| `overdue_reports` (soft) | the company is late or EDGAR is quiet. Check the company's filing index; a timely `NT 10-K` already extended the deadline. Nothing to fix in the job unless every company is "overdue" at once (then the fetch is broken) |
| `labels_far_from_calendar` (soft) | informational unless the count grows; new items need a look at the filing |

Every command's first line names the database it used (`database: host:port/name`; `report` and `summary` say it on
stderr so their JSON stays clean): read it before the checks, a green run against the wrong database is otherwise
indistinguishable from a good one. `make check` reprints the latest run's stored checks without fetching or
re-evaluating, fails if no successful run started today (UTC), and warns when `companies.csv` or `overrides.toml`
changed since that run (every run records their SHA-256 in `runs.notes`; the stored checks were evaluated against the
old files, the next `make run` re-evaluates them). Use it when the scheduler's log is gone.

`error: cannot connect to the database at HOST:PORT` means the run never started: there is no `runs` row and no checks.
Docker saying `Healthy` does not rule it out, because that check runs inside the container. If another program held the
port when the container started, `DB_PORT=<free port> make run` moves the database when `make` is the one supplying it;
with your own `DATABASE_URL` or `.env`, change the URL and `DB_PORT` together, then run `make db`. On a second machine,
with the port freed afterwards, the container stayed unreachable until `docker compose up -d --force-recreate db`.

## 6. Changing the labelling rule

The rule is `label()` in `src/sec_filings/fiscal.py`, pure and tested. Every run relabels every fetched filer with
the current rule, so a change propagates on the next run; `runs.notes->>'relabelled'` says how many rows changed.
Procedure: write the failing test first (`tests/test_fiscal.py` is table-driven with real dates), change the rule,
run the tests, `make run`, then `make verify-labels`, and compare the `labels_match_sec` counts before and
after; a change that creates real disagreements is wrong.

## 7. Completion checks: do not say "done" before all of these pass

```bash
make test                                      # all green
make test-db                                   # idempotence proof, green
make run; make run                             # twice; second says new=0, RESULT: OK
make verify-labels                             # exit 0 (no real disagreement)
make report TICKER=AAPL YEAR=2009 PERIOD=FY    # still one row, 10-K, filed 2009-10-27
make summary                                   # every company has annual rows; 10-Q rows only for domestic filers
git status --short                             # only the files you meant to change
```

And in words: every decision you made is in `docs/decisions.md` with the alternative and the check; every finding
you accepted and every correction you made is in `overrides.toml` with what you looked at; the run output is in
`docs/evidence/` with the date.

## 8. Things that look like bugs and are not

- Foreign filers have no quarterly rows (D1).
- XOM has two CIKs and its Q2 2026 10-Q lists both; it appears once (D2).
- GameStop's `fiscal_year` is one more than GameStop's own name for the year (D3); `sec_fiscal_year` shows theirs.
- `labels_far_from_calendar` warns about a dozen 1994-2015 filings whose EDGAR period is their filing date; they
  are labelled from the filing date and say so in `label_note`.
- The second `make run` of a day reports `new=0` and an empty `news` line: that is the idempotence working.

## 9. Layout

```
Makefile           the commands; `make help` lists them
src/sec_filings/   settings, edgar (client), fiscal (rule), db (schema), loader (EDGAR to table), checks, flow, verify, report, cli
src/sec_filings/sql/   001_schema.sql, the whole schema with comments, applied once (a later change is a new numbered file)
tests/             unit tests, fixtures from real EDGAR pages, a DB replay test
docs/              decisions, morning-check, what-i-didnt-trust, analyst-question, agent-setup, evidence/, review-evidence/, agent-runs/
overrides.toml     facts EDGAR does not carry: extra CIKs, fiscal year ends, naming conventions, filing notes and corrections, accepted findings
constraints.txt    the exact package versions make installs; `make lock` refreshes them
Dockerfile, .dockerignore   the job as an image for schedulers that run containers (`make docker-run`); the compose `job` service runs it
```
