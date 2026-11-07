# Hermes reviewer report: review-3-delta

- Generated: 2026-11-07 18:31 CET
- Profile: takehomereviewer (gpt-6.1-sol, reasoning max, report-only)
- Brief: /Users/homerolapida/.hermes/briefs/takehome/review-3-delta.md
- Every finding below still has to be checked against the code and data before it is acted on.

VERDICT: FAIL

Closure claims

A. PARTIAL — report.py:56-58 excludes runs with failed hard checks, and the persisted-gap probe remained a hard failure with a newer baseline; however, a successful full backfill immediately reopens the gap. See new defect C.

B. PARTIAL (major) — flow.py:117-126 retries recent unlabelled XBRL reports independently of inserts, and the refresh-error probe failed sec_labels_refreshed; but checks.py:496-499 still returns info/passed=True when there are zero SEC labels, even with overdue unverified reports. Remove that success shortcut and generate overdue accession findings in this branch too.

2. PARTIAL — The zero-label probe returned pending_sec_label=1 and older_without_sec_label=1, but still passed without a finding for the overdue accession (checks.py:496-499); the counts are present, but the verification failure remains undetected. Same fix as B.

3. CLOSED — Pure probes rejected shortened arrays for every PAGE_COLUMNS column, including reportDate, and rejected missing form/filingDate (loader.py:272-278); the persisted-gap probe also confirmed that advancing the baseline alone no longer clears an existing gap.

7. PARTIAL (major) — loader.py:405-408 still selects missing filings by primary cik only: a SELECT probe for a disappearance covered only by a secondary registrant returned false, versus true through membership. checks.py:340 also still counts canonical fl.cik: two reporting CIKs sharing one canonical owner produced no cross-CIK finding. Use registrant membership in both predicates, retaining run-wide visibility exclusions.

11. CLOSED — loader.py:425-430 repairs the legacy body on a same-hash observation; SELECT confirmed all 21 recent snapshots contain cik and the additional full-page metadata keys, with last_seen_run=7.

12. CLOSED — SELECT returned all six specified shell-period filings with non-null Churchill/SPAC notes, matching overrides.toml:67-72 and :92-103; report LCID 2020 Q2 and report LCID 2021 Q1 expose those notes.

13. CLOSED — docs/decisions.md:16 includes NT 10-K405; :27 places names on filers; :153-154 describes stored-result retrieval; :163 and :184 use four calendar months.

New defect introduced since pass 2

C. Major — The documented history-gap recovery cannot complete after a genuine outage.

  Files: loader.py:340-341, :447-450; flow.py:93, :115-116.

  ingest_filer clears coverage_gap_since after successfully reading history. The flow then passes that filer’s recent-window start to record_coverage_gaps, which compares it against the unchanged pre-outage clean-run date and immediately sets the gap again. IngestResult.history_fetched is not used to prevent this.

  Confirmation: a SELECT model of the exact update order, with previous_clean=2024-01-01, recent_start=2025-01-01 and successful history fetching, returned coverage_gap_after_full_history=NULL followed by coverage_gap_after_flow_record=2026-11-07. The resulting hard failure prevents this recovery run becoming the next clean baseline, so repeating --full repeats the failure.

  Fix: make successful history catch-up advance a per-filer coverage watermark and honor it during gap detection. Cover outage → full backfill → daily refresh, including recovery while another hard check remains unresolved.

Accepted limitations

  The previously accepted normalized fiscal-year convention, exclusion of 6-K interim disclosures, weaker pre-XBRL verification, holiday/52–53-week forecasting approximations, filing-content exclusion and historical deletions requiring --full remain unchanged.

  Run 7’s stored check command exited 0, but it exercised neither gap recovery nor label-refresh retries: its database state has no coverage gaps and its refresh count is zero.

  Verification used SELECT-only SQL, read-only CLI commands and in-memory/pure probes. No ingestion, verify-labels, pytest, live SEC requests or file writes were performed. Ford’s missing local SEC-label row and accession-specific acceptance were confirmed; the companyfacts payload was not independently fetched.
