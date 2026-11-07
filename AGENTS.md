# Handover kit: running, extending and fixing the SEC filings job

You are an agent (or a person) who has to keep this job alive without the author. Read this file, then
`README.md`, then `docs/decisions.md`. Everything else is reachable from those three. Work from the repository root;
every command below assumes it.

## 1. What this is, in five lines

A daily job keeps a Postgres table of every annual (10-K, 10-K405, 10-KT, 20-F, 40-F) and quarterly (10-Q, 10-QT)
report that the companies in `companies.csv` filed with the SEC, plus their amendments and late-filing notices, each
labelled with the fiscal year and period it covers. Source: EDGAR's submissions API. The one command is
`sec-filings run`; it exits 0 only when the run completed and every hard check passed. The `reports` view answers
"what do we have for Apple's 2009 annual report" with one row.

## 2. Set up and prove it works (ten minutes)

```bash
docker compose up -d                                      # local Postgres 17, or use any Postgres via DATABASE_URL
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
export DATABASE_URL=postgresql://sec:sec@localhost:5432/sec_filings
.venv/bin/pytest -q                                       # expect: all passed, no failures
.venv/bin/sec-filings backfill                            # first load; about 120 requests, under a minute
.venv/bin/sec-filings run                                 # second run: "new=0", RESULT: OK
.venv/bin/sec-filings report AAPL 2009 FY                 # one row, form 10-K, filed 2009-10-27, 1 amendment
.venv/bin/sec-filings verify-labels                       # loads the SEC's XBRL labels; exit 1 is normal, see 5.3
```

If `backfill` or `run` ends with `RESULT: ATTENTION NEEDED`, read the `[FAIL]` lines; section 5 says what each means.

## 3. Decisions you must not undo without re-reading their entry

The full log with alternatives and evidence is `docs/decisions.md`. The ones that bite:

- **D1 forms.** Foreign private issuers (SAP, SHEL, BABA, NVO, TM) file 20-F and never a 10-Q; their interim results
  are 6-Ks, which are deliberately not tracked. Do not "fix" the missing quarterly rows for them.
- **D2 company is not CIK.** A company can own several CIKs over time (`company_ciks`). Exxon's ticker moved to a
  new holding company CIK on 2026-07-01; the old CIK (34088) is kept through `overrides.toml`. Never key anything
  on the ticker's current CIK alone. Filings submitted by several registrants under one accession number are
  stored once, with the other CIKs in `also_filed_by_ciks`.
- **D3 labels.** `fiscal_year` = calendar year in which the fiscal year ends; quarters count from the previous year
  end. Some issuers name their years by the start (GameStop; Walmart and Toyota in older years). Do not bend the
  rule per company: the SEC's own label sits next to ours in `sec_labels` / the `reports` view for anyone who needs
  the issuer's naming. When EDGAR's "period of report" is just the filing date (an old metadata quirk, 20 cases),
  the label is derived from the last period end before the filing date (`label_method = 'rule_filing_date'`).
- **D5 never delete.** A filing that disappears from EDGAR gets `not_seen_since`, a hard check fails, and a person
  decides. Backfill reads every history page; the daily run reads only the recent page.
- **D6 fair access.** User agent with a contact address, 5 requests per second, no crawling. Do not raise the rate
  "to make it faster": the daily run is 22 requests.
- **D9 checks.** Hard checks fail the process; soft ones warn. Accepting a finding is an explicit entry in
  `overrides.toml` with what was checked. Nothing is auto-accepted.

## 4. Adding companies (the quarterly request)

1. Append the tickers to `companies.csv` (one per row, header `ticker`). Tickers are matched case-insensitively
   against `https://www.sec.gov/files/company_tickers.json`.
2. Run `.venv/bin/sec-filings backfill`. It fetches every history page of the new CIKs only once; existing
   companies are refreshed from their recent page.
3. Read the checks. Expected failures for a new company, and what to do:
   - `tickers_resolved` fails: the ticker is not in EDGAR's file (delisted, wrong ticker, or a foreign line). Find
     the CIK on https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany and add it under `[[extra_ciks]]` in
     `overrides.toml` with the evidence; keep the ticker in `companies.csv` so the company has a row.
   - `fiscal_year_end_known` fails: EDGAR has no `fiscalYearEnd` for the filer. Open the latest 10-K or 20-F, read
     the period of report, add `[[fiscal_year_ends]]` with `mmdd` and the evidence.
   - `labels_unique` fails: two originals for one (year, period). Look at both filings' cover pages. Usually one of
     them is a transition-period report or an EDGAR quirk; if both are genuine, record it under
     `[[accepted_findings]]` with the explanation.
4. If the company has predecessor CIKs (reorganisation, SPAC, name change with a new CIK), add them under
   `[[extra_ciks]]` with `valid_to` and the evidence. Hints that this is needed: the first filing is recent while the
   company is old; `formerNames` in the submissions JSON; an `8-K12B` or `8-K12G3` form in its first filings.
5. Run `.venv/bin/sec-filings verify-labels`, then `.venv/bin/sec-filings run`. Any new item under
   `labels_match_sec` is either the issuer's naming convention (reported as a count, nothing to do) or a real
   disagreement: open the filing's cover page, decide who is right, and record it under `[[accepted_findings]]`
   if the SEC label is the wrong one.
6. Spot-check by hand: pick one fiscal year of the new company, run `sec-filings report TICKER YEAR FY`, open
   `primary_document_url`, and confirm the cover page's "for the fiscal year ended" matches the label.
7. Add a test case for the new fiscal year end pattern to `tests/test_fiscal.py` if the pattern is new (section 6).
8. Copy the run output into `docs/evidence/` with the date, add a dated entry to `docs/decisions.md` if any
   decision was needed, then run the completion checks in section 7.

## 5. When the morning check fails

| failing check | first action |
|---|---|
| `run_complete` | read the stderr log for the CIK that failed; rerun `sec-filings run` (idempotent); if EDGAR is down, the retry log shows 429/5xx |
| `tickers_resolved` | section 4, step 3 |
| `cik_changes_acknowledged` | the ticker moved to a new CIK. Compare both CIKs' submissions JSON (`name`, `formerNames`, first filing). Add the old CIK under `[[extra_ciks]]` with `valid_to` and the evidence. Both CIKs keep being fetched; nothing is lost meanwhile |
| `fiscal_year_end_known` | section 4, step 3 |
| `no_filings_lost` | the feed stopped listing a filing. Open its index URL. If EDGAR removed it (post-acceptance deletion), leave the row with its `not_seen_since` and record the reason as an accepted finding; if it reappears, the flag clears itself |
| `labels_complete` | a report has no label: usually `no_report_date` (EDGAR gave none) or `no_fye`. Fix the input (override), never the row by hand |
| `labels_unique` | section 4, step 3 |
| `overdue_reports` (soft) | the company is late or EDGAR is quiet. Check the company's filing index; if a `NT 10-K` appeared, the check already extended the deadline. Nothing to fix in the job unless every company is "overdue" at once (then the fetch is broken) |
| `labels_far_from_calendar` (soft) | informational unless the count grows; new items need a look at the filing |
| `labels_match_sec` (soft) | section 4, step 5 |

`.venv/bin/sec-filings check` reprints the latest run's checks without fetching, and fails if no successful run
started today (UTC). Use it when the scheduler's log is gone.

## 6. Changing the labelling rule

The rule is `label()` in `src/sec_filings/fiscal.py`, pure and tested. Every run relabels every filing with the
current rule (`relabel_filer`), so a change propagates on the next run; `runs.notes->>'relabelled'` says how many
rows changed. Procedure: write the failing test first (`tests/test_fiscal.py` is table-driven with real dates),
change the rule, run the tests, run `sec-filings run`, then `verify-labels`, and compare the `labels_match_sec`
count before and after; a change that increases real disagreements is wrong.

## 7. Completion checks: do not say "done" before all of these pass

```bash
.venv/bin/pytest -q                                                                   # all green
TEST_DATABASE_URL=$DATABASE_URL .venv/bin/pytest -q tests/test_replay_db.py          # idempotence proof, green
.venv/bin/sec-filings run && .venv/bin/sec-filings run                               # twice; second says new=0, RESULT: OK
.venv/bin/sec-filings report AAPL 2009 FY                                            # still one row, 10-K, filed 2009-10-27
.venv/bin/sec-filings summary                                                        # every company has annual rows; 10-Q rows only for domestic filers
git status --short                                                                   # only the files you meant to change
```

And in words: every decision you made is in `docs/decisions.md` with the alternative and the check; every finding
you accepted is in `overrides.toml` with what you looked at; the run output is in `docs/evidence/` with the date.

## 8. Things that look like bugs and are not

- Five companies have no quarterly rows (D1).
- XOM has two CIKs and its Q2 2026 10-Q lists both (D2).
- GameStop's `fiscal_year` is one more than GameStop's own name for the year (D3); `sec_fiscal_year` shows theirs.
- `verify-labels` exits 1 while four SEC labels are known to be wrong (listed in `overrides.toml`); the morning
  check reports them as accepted.
- The second `run` of a day reports `new=0` and a `news` line with nothing: that is the idempotence working.

## 9. Layout

```
src/sec_filings/   settings, edgar (client), fiscal (rule), db (migrations), loader, checks, flow, verify, report, cli
src/sec_filings/sql/   numbered schema files, applied once each
tests/             unit tests, fixtures from real EDGAR pages, a DB replay test
docs/              decisions, morning-check, what-i-didnt-trust, analyst-question, agent-setup, evidence/, review-evidence/
overrides.toml     facts EDGAR does not carry: extra CIKs, fiscal year ends, accepted findings
```
