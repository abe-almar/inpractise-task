# The morning check

If tomorrow's 06:00 run is wrong, what tells us? Not the exit code of a crash: a run can finish green and still be
wrong. So every run ends by evaluating twelve named conditions against the database and recording them in the
`checks` table, and the process exits 0 only when the run completed **and** every hard check passed. The scheduler
alerts on a non-zero exit; a person (or an agent) then runs `sec-filings check`, which prints the latest run's checks
without touching EDGAR, and adds one more condition: that a successful run started today.

## What "wrong" means here

| check | severity | it fails when | why that would be wrong |
|---|---|---|---|
| `run_complete` | hard | fewer filer pages were fetched than companies and CIKs on the list | a partial run looks like "no news" for the companies it skipped |
| `tickers_resolved` | hard | a ticker in `companies.csv` is not in `company_tickers.json` | the company would silently stop being tracked |
| `cik_changes_acknowledged` | hard | a ticker now maps to a different CIK than before, and nobody has recorded the old CIK in `overrides.toml` | Exxon did exactly this on 2026-07-01; a job keyed on the new CIK would lose thirty years of filings |
| `fiscal_year_end_known` | hard | a filer has no fiscal year end in EDGAR and no override | no label can be derived (Alibaba's is null in EDGAR) |
| `no_filings_lost` | hard | a filing we had seen is no longer listed in the window the recent page covers | EDGAR does delete filings after acceptance; the row stays, flagged `not_seen_since`, and someone decides |
| `labels_complete` | hard | a report has no fiscal label | the one-row question cannot be answered |
| `labels_unique` | hard | two original reports share (company, year, period) under one CIK | the one-row question has two answers; the first backfill found four, all EDGAR metadata quirks |
| `http_health` | soft | EDGAR returned errors (even if retries succeeded) or the run took longer than the budget | an early sign of rate limiting or an outage |
| `labels_far_from_calendar` | soft | a report's period of report is not on the company's fiscal calendar | the label is best effort; 20 such cases come from EDGAR storing the filing date as period of report |
| `overdue_reports` | soft | a company is past the statutory deadline for its next report and EDGAR shows nothing | either the company is late (that is news) or the job stopped seeing new filings (that is a bug) |
| `labels_match_sec` | soft | our label disagrees with the SEC's XBRL label in a way that is not the issuer's naming convention | a real disagreement is either an SEC data error or ours; both are worth a look |
| `news` | info | never fails | lists the new filings, amendments and late-filing notices of the run |

Findings a person has examined and accepted are listed in `overrides.toml` under `[[accepted_findings]]` with the
check name and the key the check printed; they are then reported as information instead of failing. Nothing is ever
auto-accepted.

## The 06:00 run, concretely

1. The scheduler runs `sec-filings run` and gets exit code 0 or 1.
2. On 1, the alert carries the printed report. The first line gives the run id, filers fetched, new filings and
   requests; each check follows with its items. `sec-filings check` reprints it later.
3. On 0, nobody has to look. The `news` line in the log says what came in, which is what a human would want to
   skim anyway (for example "2 new filings, of which 1 amendment").

## What this does not catch

- A filing EDGAR has not published yet. The submissions API is near real time, and filings accepted after 17:30 ET
  are disseminated the next business day, so a 06:00 European run sees everything through the previous US business
  day. `overdue_reports` catches the case where that silence lasts past the deadline.
- A wrong fiscal year end in EDGAR for a company whose XBRL labels also follow it. The SEC cross-check would agree
  with the mistake. The `labels_far_from_calendar` check would still flag the resulting off-calendar period ends.
- Federal holidays in the deadline arithmetic (weekends are handled). A report can be reported overdue a day early
  around a holiday; it is a soft check for that reason.
- Changes in the content of a filing. The job tracks filings, not what is in them.

## Trying it

The checks ran clean on the second run of 2026-11-07 (`docs/evidence/daily-run-2-2026-11-07.log`). To see a hard
failure, rename a ticker in `companies.csv` to one that does not exist and run `sec-filings run`: `tickers_resolved`
fails, the exit code is 1, and the next run with the ticker restored is green again.
