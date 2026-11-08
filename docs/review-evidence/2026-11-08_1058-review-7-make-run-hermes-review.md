# Hermes reviewer report: review-7-make-run

- Generated: 2026-11-08 10:58 CET
- Profile: takehomereviewer (gpt-6.1-sol, reasoning max, report-only)
- Brief: /Users/homerolapida/.hermes/briefs/takehome/review-7-make-run.md
- Every finding below still has to be checked against the code and data before it is acted on.

VERDICT: FAIL

MAJOR — src/sec_filings/loader.py:271-289,432-434; tests/test_replay_db.py:675-704
Automatic bootstrap can still certify incomplete history.

    History responses are passed straight to parse_page without validating the flat history block or its advertised filing count. An empty JSON object is accepted as a successfully read page.

    Confirmed with an isolated in-memory ingestion probe: a valid recent page plus two advertised history pages returning {} produced pages=3, rows=1, history_fetched=True and reached commit. The coverage update marks backfill complete; the next daily probe fetched only the recent page. check_run_complete also passes the resulting successful-filer counts. Missing, never-loaded reports provide no rows for the label or disappearance checks to inspect.

    This weakness already existed in the history reader; the new daily-bootstrap guarantee now depends on it.

    Fix: validate historical blocks before accepting them, including required columns, consistent lengths and advertised counts. Raise EdgarError on invalid pages so the filer transaction rolls back. Add first-daily-run cases for empty/truncated history and a failure midway through history.

MAJOR — Makefile:7-8,53; README.md:21-23
Database configuration is not parsed consistently or preserved literally.

    Make sources .env as shell, whereas settings.load_dotenv treats its values as literal text. Synthetic .env probes confirmed:
    - DATABASE_URL = postgresql://remote.example.invalid/filings is accepted by the Python reader but fails as shell.
    - An unquoted URL containing ?sslmode=require&application_name=reviewer leaves the sourcing shell without DATABASE_URL.
    - In both cases, Make selects and exports the local default. The job’s setdefault-based reader cannot restore the intended remote connection. This can run successfully against the wrong database.

    Separately, GNU Make 3.81 child-environment probes confirmed that a quoted command-line assignment containing application_name=sec$tag reaches the child as secag. An environment-supplied value retains the dollar sign, although Make’s expanded comparison sees a different value.

    Fix: resolve configuration once with a non-executing parser and use that exact value for both database startup and the job. Do not silently substitute the local database after a supplied configuration fails interpretation. Preserve or explicitly constrain command-line values.

MINOR — docs/decisions.md:128-129; docs/morning-check.md:17; tests/test_replay_db.py:738-746
The inherited-gap exception was removed from the test, not eliminated from possible input state.

    A database written by the previous daily path can contain a gap with backfill_completed_at NULL. The new daily rule reads its history and clears that gap without make backfill or human acknowledgement.

    The in-memory probe confirmed this; a completed-backfill filer with a gap still retained its alarm. The rewritten test covers only the latter case. Consequently, the blanket “until make backfill” and human-review wording is stronger than the implementation.

    Fix: restore the inherited-state case, asserting full history and gap clearance on success, and preservation on failure. Document the bootstrap exception. Also qualify README.md:18’s one-page claim as applying to history-complete CIKs.

MINOR — Makefile:10,48; README.md:8
The advertised Python 3.12+ prerequisite is insufficient for the literal command.

    Setup invokes python3.12 specifically. Confirmed on this machine using PATH=/usr/local/bin:/usr/bin:/bin: python3 is Python 3.14.6, make exists, but python3.12 does not. The forced setup dry-run still selects python3.12.

    Fix: select python3 and validate its version, or document the exact executable requirement and the PYTHON override.

MINOR — Makefile:45-50
The installation stamp can mask a broken virtual environment.

    Setup depends only on .venv/.installed and pyproject.toml, not executable availability or importability. Dependency inspection and dry-runs confirmed that setup remains “nothing to be done” while a nonexistent CLI path is selected.

    Fix: validate the interpreter and CLI before reusing the stamp, or provide a documented rebuild target and recovery instruction.

Other delta checks

    The pass-6 D3 finding is closed: docs/decisions.md:47-50 now states the off-calendar condition and both 14-day thresholds; the previous pure-function probes reproduced the implementation.

    The removed NULL-watermark branch is unreachable through the current supported writers. _upsert_filer creates the row before _advance_coverage; successful history completion sets the timestamp and watermark together. A midway HTTP failure rolls back that filer’s pending metadata and snapshots; previously committed run/resolution rows and other filers remain. The next daily attempt retries all history. Bare filer rows and co-registrants do not bypass this initialization.

    Read-only SQL found 21 filers, no NULL watermarks, and no completed-backfill/NULL-watermark combinations. Deliberately inconsistent or absent rows do raise the questioned TypeErrors, but no current supported writer producing them was found.

    A new CIK’s full read does not acknowledge the old CIK: cik_changes_acknowledged still queries the retained stale mapping. History widens the disappearance window; cumulative seen accessions and subsequent co-registrant upserts prevent a surviving shared accession from remaining flagged. First-successful-registrant ownership is unchanged.

    No runtime one-page cap was found. The SEC-label queue includes insertions from history and outstanding labels regardless of runs.mode. The 600-second budget is a soft warning, not a timeout. GNU Make 3.81 accepted the dry-run targets. README scheduling supplies the required cwd change; ordinary recipe failures remain non-zero to the scheduler.

Accepted limitations

    No job command or test suite was executed, and no files were changed. History-failure probes were in memory, not live SEC failures or database writes. The clean-clone log was reviewed, not reproduced. A separate clean Linux installation and concurrent executions were not exercised.
