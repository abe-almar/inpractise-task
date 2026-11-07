# Hermes reviewer report: review-4-delta

- Generated: 2026-11-07 18:40 CET
- Profile: takehomereviewer (gpt-6.1-sol, reasoning max, report-only)
- Brief: /Users/homerolapida/.hermes/briefs/takehome/review-4-delta.md
- Every finding below still has to be checked against the code and data before it is acted on.

VERDICT: FAIL

Closure claims

C / A. PARTIAL — runs/checks 9–10 retain the hard gap failure, and 11–12 clear it after backfill; the run-level baseline no longer controls coverage. However, initialising a null watermark can erase an inherited gap without reading history; see the major defect below.

B / 2. PARTIAL (minor) — checks.py:489–518 produced hard no_sec_label findings in probes with both zero and nonzero comparisons; acceptance exclusion and both-mode refresh selection are present. But loader.py:477 selected the same 30 of 31 eligible synthetic filers on successive SELECT probes, leaving filer 31 unselected while those filings remain unlabelled. Add oldest-attempt ordering or rotation to guarantee eventual retries.

7. CLOSED — loader.py:405–408 and checks.py:330–338 passed SELECT probes: a secondary-only disappearance was selected, run-visible accessions were excluded, two originals sharing one canonical owner triggered the cross-CIK finding, and one accession with two registrants did not.

New defect introduced since pass 3

Major — Migration can silently discard an existing coverage gap.

  Files: src/sec_filings/sql/006_coverage_watermark.sql:4; src/sec_filings/loader.py:452–456; src/sec_filings/checks.py:228–238.

  Migration 006 adds coverage_through without initialising existing rows. _advance_coverage treats through IS NULL like a completed history read: it sets coverage to today and clears coverage_gap_since even when history_fetched=False. An existing backfill completion marker remains, so an ordinary subsequent backfill can also skip the missing history.

  Confirmation: an in-memory invocation of the actual helper emitted the gap-clearing UPDATE with history_fetched=False. A SELECT-only projection of that statement took an inherited gap of 2026-11-06 and a null watermark to gap=NULL and coverage=2026-11-07, retaining the old completion marker. check_history_gap then passes on that cleared state. This is a false-green path, not merely missing evidence.

  Fix: preserve inherited gaps. Initialise watermarks from demonstrably covered prior observations, or require a history read before declaring unknown coverage complete. Cover upgrade → daily refresh → backfill → daily refresh.

Accepted limitations

  The live sequence proves gap-state detection, persistence and clearing, not recovery of an actually missed filing. The selected XOM filer fetched only its recent page in run 11; SELECT found zero historical snapshots for it. Other filers’ missing completion markers caused the broader historical reread.

  Runs 8–12 refreshed zero SEC-label filers. Retry conclusions here rest on code and probes, not a live retry.

  Previously accepted fiscal-year conventions, 6-K exclusion, weaker pre-XBRL verification, deadline approximations, filing-content exclusion and historical deletions requiring --full remain unchanged.

  sec-filings check exited 0 for stored run 12. No files were written; no ingestion, verify-labels, pytest or live SEC requests were performed.
