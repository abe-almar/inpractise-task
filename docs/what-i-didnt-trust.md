# What could still go wrong

These are potential failures that the current checks may not catch or prevent. The scheduler is assumed to
notify us about missed starts, hangs and failed jobs.

**A warning could be missed because the job still reports success.** Overdue reports, unusual fiscal dates and
recovered HTTP errors produce warnings but allow `RESULT: OK` and exit 0. If we only look at failed jobs, a
warning about missing data could go unnoticed. New warnings need review alongside failures.

**SEC could return incomplete or outdated data that looks valid.** The job cannot reliably detect a filing
that SEC has never listed in the feed. Daily runs also do not reread every historical page, so older changes
could remain unnoticed. Periodic `make backfill FULL=1` would pick up changes present in those pages, but cannot
prove that SEC's source is complete.

**A report could be assigned the wrong fiscal year or quarter.** Comparing our labels with SEC's does not
guarantee correctness: both can share an error. Reports without XBRL data lack that comparison, and new XBRL
reports get 21 days for missing SEC labels to arrive. Existing SEC labels are reused between refreshes, so a
correction may not be picked up immediately. Periodic `make verify-labels` and checking report covers reduce
this risk.

**A configuration change could quietly reduce coverage or hide a problem.** Accidentally removing a company
from `companies.csv` changes what the job expects to track, so the smaller scope can still pass. Accepted
exceptions in `overrides.toml` have no expiry and match a check and key; a different problem with the same key
could also be suppressed. Company-list changes need review, and exceptions need to be rechecked against their
supporting evidence.

**A failed job could leave data that appears fully updated.** Each company's successful updates are saved
separately. If a later step fails, queries can still return a mixture of fresh and older data. Before treating
the table as a complete refresh, consumers need to check the latest run's overall verdict, including its hard
checks. The execution status `ok` alone does not establish that the data passed those checks.
