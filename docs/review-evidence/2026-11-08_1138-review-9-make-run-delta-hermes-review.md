# Hermes reviewer report: review-9-make-run-delta

- Generated: 2026-11-08 11:38 CET
- Profile: takehomereviewer (gpt-6.1-sol, reasoning max, report-only)
- Brief: /Users/homerolapida/.hermes/briefs/takehome/review-9-make-run-delta.md
- Every finding below still has to be checked against the code and data before it is acted on.

VERDICT: PASS

Findings: none confirmed in git diff c9a5046..HEAD.

Closure checks

F1 — Makefile:7-10; src/sec_filings/settings.py:16-30,45-52
The wrong-database fallback is closed. Make dry-runs and in-memory probes of the actual settings reader confirmed:
    Leading tabs, mixed export whitespace, spaces and unquoted & no longer cause Make to override the supplied URL.
    Empty, comment-only and similar-key-only .env inputs suppress Docker and fail with “DATABASE_URL is not set”.
    A directory named .env likewise suppresses Docker and fails configuration.
    An empty DATABASE_URL environment variable fails configuration, even with a valid URL in .env; it does not silently select another database.
    A copied .env.example supplies the compose URL but does not start Docker: make db is required.
    A scheduler’s nonempty DATABASE_URL takes precedence over an unexpected .env. Without that environment variable, an unexpected .env lacking a URL stops the job rather than falling back.

Presence cases were exercised using existing file/directory wildcard probes and mocked .env contents; no .env was created or read.

Documentation — README.md:22-25; AGENTS.md:26-28; docs/decisions.md:128-140; .env.example:1-4
These describe the same ownership rule: Make supplies and starts compose only when both DATABASE_URL and .env are absent. Otherwise configuration belongs to the job; compose startup is manual.

F3 — Makefile:50-51
Both interpreter invocations preserve a pathname containing spaces as one shell argument in dry-runs. The guard parses with Python 3.4 grammar. Actual Python 3.9 execution exits 1 with the intended version diagnostic. The python3-only PATH fallback also selected python3.

F4 — docs/morning-check.md:17
The revised condition matches loader.py:284-305,436-455: a history read clears the gap, including an automatic bootstrap retry for a never-read filer. It no longer claims only the backfill command can clear it.

Accepted limitations

F2 — docs/decisions.md:96-100
Accepted, not fixed. A read-only transaction over the latest source_snapshots independently reproduced all recorded numbers: 96 advertised/stored history pages, 96 count matches, 96 filingFrom matches and 92 filingTo mismatches. No evidence established a realistic EDGAR wrong-page delivery, so the simulated substitution is not re-reported.

No first-run Makefile defect was confirmed within the permitted checks and stated prerequisites. Installation, Docker startup, ingestion and Linux execution were not rerun; those remain supported by the supplied evidence log. No suites or job commands were executed. No files were written; tracked worktree changes remained absent.
