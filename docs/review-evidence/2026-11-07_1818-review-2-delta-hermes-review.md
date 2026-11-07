# Hermes reviewer report: review-2-delta

- Generated: 2026-11-07 18:18 CET
- Profile: takehomereviewer (gpt-6.1-sol, reasoning max, report-only)
- Brief: /Users/homerolapida/.hermes/briefs/takehome/review-2-delta.md
- Every finding below still has to be checked against the code and data before it is acted on.

VERDICT: FAIL

Closures

1. CLOSED — src/sec_filings/checks.py:235-255 reads persisted disappearance flags; loader.py:362 clears them on reappearance, and :318-324 expands the backfill window. Historical-deletion coverage is qualified in docs/morning-check.md:49-50.

2. PARTIAL — Explicit conventions and hard disagreements are implemented, but SEC refresh is triggered only by new inserts (flow.py:94-120), with no durable retry for lagging labels or failed refreshes. With zero SEC labels, checks.py:470-471 passes without reporting the coverage counts. See new defect B.

3. PARTIAL — Wrong CIK, empty recent block and shortened filingDate were rejected by probes; a shortened reportDate array was accepted and parsed into a missing date (loader.py:260-272). History-gap detection also self-clears without repair; see new defect A. Validate all supplied column lengths and retain unresolved coverage gaps.

4. CLOSED — The boundary probe returned 2026 FY for 2027-01-02 against 1231. SQL and report AAPL 1997 Q2 confirmed the corrected amendment attachment, with raw report_date retained; both Chemical Banking acceptances are present in overrides.toml:82-90.

5. CLOSED — A pure-function probe selected historical 0228 for 2026-02-28 and current 0630 for 2026-06-30; loader.py:484-486 applies that selection per filing. D4 no longer promises a fiscal-calendar table.

6. CLOSED — loader.py:162-173 resolves absent tickers through current override links; :121-129 persists the current flag. AGENTS.md:69-71 describes that supported recovery path.

7. PARTIAL — SQL confirmed Exxon’s canonical file number and URL are repaired, but disappearance and cross-CIK checks still join only the primary CIK (loader.py:391-394; checks.py:313-316). A synthetic SELECT found two originals through reporting membership but only one through the checks. Use registrant membership consistently in availability and conflict checks.

8. CLOSED — The 51-item acceptance probe remained a hard failure with filing-50 outstanding. SELECT probes confirmed null period, null method and unknown period are now detected; checks.py:262-275 retains the complete finding list.

9. CLOSED — Stored deadlines now show BABA/TM 2027-08-02 and GME 2026-12-10. checks.py:419-424 restricts NT grace by period proximity, annual/quarterly family and filing timeliness.

10. CLOSED — loader.py:313-328 gates historical-page fetching on backfill_completed_at, with --full bypassing that gate; AGENTS.md:61-62 matches the implementation.

11. PARTIAL — All 21 stored recent snapshots still contain only category, filings, fiscalYearEnd and name, despite last_seen_run=5. loader.py:411-417 updates observation metadata but never repairs the legacy truncated body. Store the complete body on a same-hash observation too.

12. PARTIAL — LCID’s FY2020 annual note is visible, but SQL found null notes on its 2020 Q2 and Q3 reports. overrides.toml:67-72 covers only the annual and its amendment. Extend the provenance note to the remaining shell-period reports.

13. PARTIAL — Code/interface fixes are present, but docs/decisions.md still gives companies a name column (:27), says check reruns checks (:153-154), omits NT 10-K405 from D1 (:16-17), and retains 120-day 20-F descriptions (:162, :182). Correct those remaining contradictions.

New defects introduced by the changes

A. Major — The new history_gap alarm can disappear without a backfill.

   Evidence: report.py:55-57 selects the previous run by status='ok', ignoring checks_passed. flow.py:104 leaves that status on runs whose hard checks subsequently fail. The database already contains run 4 with status=ok and checks_passed=false. A constructed check_history_gap probe failed against the old baseline, then passed against the newer run date without any history repair.

   Fix: retain unresolved coverage gaps per CIK until history catch-up succeeds. Do not advance coverage merely because a run reached its checks; explicitly recognize successful full backfills.

B. Major — The new daily SEC-refresh path loses unfinished verification work.

   Evidence: ingestion commits at loader.py:329 before refresh; flow.py:94-120 queues only newly inserted accessions’ CIKs. After a crash, failed request, or successful response whose facts have not caught up, the next daily run sees updates rather than inserts and does not retry. A constructed refresh-error probe left run_complete passing and HTTP health only soft. Pending/older-unlabelled counts do not fail labels_match_sec.

   Fix: persist pending verification work and retry independently of new inserts. Distinguish unavailable amendment facts from overdue verification, and require attention when verification fails or exceeds its allowed lag.

Accepted limitations

- The normalized nominal-year convention, exclusion of 6-K interim disclosures, and weaker pre-XBRL verification remain accepted.
- Federal holidays, approximate 52/53-week deadline forecasting, filing-content changes, and historical deletions requiring --full remain explicitly limited.
- The existing database has no populated backfill_completed_at markers, so its next backfill will reread legacy histories once.
- Verification used SELECT-only SQL, pure-function probes, and check/report/summary; those CLI commands exited 0. No ingestion commands, verify-labels, pytest, EDGAR requests, file writes or database mutations were performed.
