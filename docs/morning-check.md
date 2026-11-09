# The morning check

Every `make run` checks the fetched data and the stored filings against sixteen conditions, then saves the results
in the `checks` table. It exits 0 only when execution completed and every hard check passed. The scheduler is
responsible for notifying us about missed starts, hangs and failed jobs; these checks identify data problems in a
run that reached the end of its work.

Hard failures print `[FAIL]` and produce `RESULT: ATTENTION NEEDED`. Soft failures print `[WARN]` but still allow
exit 0, so new warnings need review alongside failures. Informational checks report news without failing the run.
The `runs.status = 'ok'` execution status alone is insufficient: `runs.checks_passed` must also be true.

| Check | Severity | What it flags |
|---|---|---|
| `run_complete` | Hard | A filer was not fetched successfully, or a page failed validation: wrong CIK, empty or inconsistent recent data, or a history page with the wrong filing count. |
| `tickers_resolved` | Hard | A requested ticker is absent from the SEC ticker map and has no current CIK override. |
| `cik_changes_acknowledged` | Hard | A ticker moved to another CIK and the previous CIK has not been recorded in `overrides.toml`. Both remain tracked. |
| `fiscal_year_end_known` | Hard | A filer has no usable fiscal year end in its metadata or an override. |
| `history_gap` | Hard | The recent page starts after the date through which coverage was established. The gap persists until `make backfill` reads that filer's history. |
| `sec_labels_refreshed` | Hard | A required refresh of SEC XBRL labels failed. Unlabelled filings cause another attempt on a later run. |
| `no_filings_lost` | Hard | A previously seen filing disappeared from the checked window. It stays stored and flagged until it reappears or the finding is accepted. Normal movement into an archive is checked before flagging it. |
| `labels_complete` | Hard | An annual or quarterly report lacks a usable fiscal year, period or labelling method. |
| `labels_unique` | Hard | Multiple original reports share a company, CIK, family, fiscal year and period. |
| `labels_inferred_for_amendments` | Hard | An amendment's label was inferred from its filing date and has not been checked against the document. |
| `labels_match_sec` | Hard | Our fiscal label disagrees with SEC's outside a documented naming convention, or an XBRL report has lacked a SEC label for at least 21 days. |
| `http_health` | Soft | EDGAR requests encountered errors, even if retries recovered, or execution exceeded the 600-second budget. This budget is a warning threshold, not a timeout. |
| `labels_unique_across_ciks` | Soft | Different CIKs of one company filed different originals for the same period. |
| `labels_far_from_calendar` | Soft | A period end is too far from the fiscal calendar for a reliable label. Filing-date inferences are also listed for information. |
| `overdue_reports` | Soft | The next expected report is past its estimated filing deadline, or a Canadian `40-F` deadline needs a Canadian filing calendar. |
| `news` | Info | New filings, amendments and late-filing notices, plus SEC-label refresh results. |

To investigate a failure:

1. Read the database address and run ID in the output, then the `[FAIL]` items. If the log is unavailable, run
   `make check` to print the latest run's stored results without fetching from EDGAR or re-evaluating the data.
2. Retry `make run` after a temporary fetch failure. Use `make backfill` for a recorded coverage gap. For a fiscal
   label or CIK finding, inspect the source document and follow the [handover kit](../AGENTS.md); record corrections
   and reviewed exceptions in `overrides.toml`.
3. Run `make run` again after changing inputs or corrections, and confirm `RESULT: OK`. Review new `[WARN]` items
   as well. `make check` alone cannot apply a changed input or refresh an earlier verdict.

`make check` also fails unless the latest run started today UTC, completed successfully and passed its hard checks.
This checks the calendar day; it does not establish that the scheduled 06:00 run happened. It warns when `companies.csv` or
`overrides.toml` differs from the files used by that run, comparing their recorded SHA-256 hashes. A changed input
does not alter the stored verdict or the exit code.

An exception after a run starts is recorded as a failed run when the database is available. A failure before the
run starts, such as an unreachable database, has no new `runs` row; use the scheduler's failure log. A terminated
process can leave a row marked `running`.

Accepted findings must name the check and item key under `[[accepted_findings]]` and explain what was examined.
Accepted items remain in the stored check details; a fully accepted finding is reported as information. Nothing
is accepted automatically. See [potential risks](what-i-didnt-trust.md) for what can still go wrong despite passing
checks, including incomplete source data and partially updated data after a failure.
