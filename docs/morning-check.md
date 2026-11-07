# The morning check

If tomorrow's 06:00 run is wrong, what tells us? Not the exit code of a crash: a run can finish green and still be
wrong. So every run ends by evaluating fifteen named conditions against the database and recording them in the
`checks` table, and the process exits 0 only when the run completed **and** every hard check passed. The scheduler
alerts on a non-zero exit; a person (or an agent) then runs `sec-filings check`, which prints the latest run's
stored checks without touching EDGAR, and adds one condition of its own: that a successful run started today.

## What "wrong" means here

| check | severity | it fails when | why that would be wrong |
|---|---|---|---|
| `run_complete` | hard | a filer page could not be fetched, is for another CIK, has an empty or inconsistent recent block | a partial or empty page looks like "no news" |
| `tickers_resolved` | hard | a ticker in `companies.csv` is not in `company_tickers.json` and has no evidenced CIK override | the company would silently stop being tracked |
| `cik_changes_acknowledged` | hard | a ticker now maps to a different CIK than before, and nobody has recorded the old CIK in `overrides.toml` | Exxon did exactly this on 2026-07-01; a job keyed on the new CIK loses thirty years |
| `fiscal_year_end_known` | hard | a filer has no fiscal year end in EDGAR and no override | no label can be derived (Alibaba's is null in EDGAR) |
| `history_gap` | hard | the previous successful run is older than the oldest filing on a filer's recent page | filings could have entered and left the page during the outage; a backfill is needed |
| `no_filings_lost` | hard | any filing we have seen is no longer listed in the window the page covers, until it reappears or is accepted | EDGAR does delete after acceptance; the row stays, flagged, and someone decides; the alarm does not fade after one morning |
| `labels_complete` | hard | a report has no fiscal year, period or method | the one-row question cannot be answered |
| `labels_unique` | hard | two original reports share (company, year, period) under one CIK | two answers to the one-row question; the first backfill found four, all EDGAR metadata quirks; the fresh agent found a fifth (Disney, period a year off) |
| `labels_inferred_for_amendments` | hard | an amendment's EDGAR period is its filing date and its label came from inference, not yet read and accepted | the inference pointed at the wrong quarter once (Apple, 1997) |
| `labels_match_sec` | hard | our label and the SEC's XBRL label disagree in a way that is not a listed naming convention or an accepted finding | either an SEC data error or ours; both get a look |
| `http_health` | soft | EDGAR returned errors (even if retries succeeded) or the run took longer than the budget | an early sign of rate limiting or an outage |
| `labels_unique_across_ciks` | soft | two CIKs of the same company both filed an original for one period | normal in a reorganisation year; worth a look |
| `labels_far_from_calendar` | soft | a report's period of report is not on the company's fiscal calendar | the label is best effort; most cases are EDGAR storing the filing date as the period |
| `overdue_reports` | soft | a company is past the statutory deadline for its next report and EDGAR shows nothing | either the company is late (news) or the job stopped seeing new filings (a bug) |
| `news` | info | never fails | the new filings, amendments and late-filing notices of the run, and which filers had their SEC labels refreshed |

Findings a person has examined and accepted are listed in `overrides.toml` under `[[accepted_findings]]` with the
check name, the key the check printed and what was read; they are reported as information instead of failing. The
whole item list is evaluated against the acceptances, never a truncation. Nothing is auto-accepted, naming
conventions included: a company counts as "naming its year by the start" only when listed with evidence.

## The 06:00 run, concretely

1. The scheduler runs `sec-filings run` and gets exit code 0 or 1.
2. On 1, the alert carries the printed report. The first line gives the run id, filers fetched and validated, new
   filings and requests; each check follows with its items. `sec-filings check` reprints it later.
3. On 0, nobody has to look. The `news` line says what came in ("2 new filings, of which 1 amendment"), and whether
   the SEC labels of the filers with news were refreshed for the cross-check.

## What this does not catch

- A filing EDGAR has not published yet. Filings accepted after 17:30 ET are disseminated the next business day, so
  a 06:00 European run sees everything through the previous US business day. `overdue_reports` catches the case
  where that silence lasts past the deadline.
- A wrong fiscal year end in EDGAR for a company whose XBRL labels also follow it. `labels_far_from_calendar` would
  still flag the off-calendar period ends.
- A deletion outside the window the daily page covers (older than the oldest filing on the recent page). A
  `backfill --full` re-reads every page and flags those too.
- Federal holidays in the deadline arithmetic (weekends are handled), and the deadline of a 52/53-week quarter,
  which is taken as thirteen weeks after the last report: a day or two of slack, which is why it is a soft check.
- Changes in the content of a filing. The job tracks filings, not what is in them.

## Trying it

The checks ran clean on run 5 of 2026-11-07 (`docs/evidence/daily-run-5-2026-11-07.log`), after a review pass had
shown that two of them could stay silent (`docs/review-evidence/`). To see a hard failure, rename a ticker in
`companies.csv` to one that does not exist and run `sec-filings run`: `tickers_resolved` fails, the exit code is 1,
and the next run with the ticker restored is green again.
