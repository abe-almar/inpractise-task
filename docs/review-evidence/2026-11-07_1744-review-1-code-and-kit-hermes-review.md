# Hermes reviewer report: review-1-code-and-kit

- Generated: 2026-11-07 17:44 CET
- Profile: takehomereviewer (gpt-6.1-sol, reasoning max, report-only)
- Brief: /Users/homerolapida/.hermes/briefs/takehome/review-1-code-and-kit.md
- Every finding below still has to be checked against the code and data before it is acted on.

VERDICT: FAIL

Major findings

1. Disappearances stop failing without anyone accepting them; a crash can erase the alert entirely.

   Where: src/sec_filings/loader.py:249-250,293-309; src/sec_filings/checks.py:197-207; src/sec_filings/flow.py:71-98.

   Confirmation: _mark_missing commits not_seen_since but excludes already-flagged rows on subsequent runs. check_no_filings_lost fails only on facts.newly_missing, not on outstanding unaccepted disappearances. A read-only Python probe with one persisted disappearance and no new disappearance returned passed=True and RunResult.ok=True.

   Consequently, a crash after the ingest commit but before checks means the next run can be green without ever reporting that deletion. Deletions outside the recent-page window are not checked even during backfill.

   Fix: derive unresolved disappearance findings from persistent database state on every check. Clear them only on reappearance or explicit acceptance. Define and document historical-deletion coverage.

2. The SEC cross-check automatically excuses arbitrary one-year errors and does not check newly arriving filings.

   Where: src/sec_filings/checks.py:337-374; src/sec_filings/flow.py:69-98; src/sec_filings/verify.py:42-65; docs/morning-check.md:26-35.

   Confirmation: is_convention accepts any ±1-year difference with the same period, for any company. A function probe comparing an incorrect AAPL 2025 Q1 against SEC 2026 Q1 passed and described Apple as naming years by their start. A read-only projection subtracting one from every AAPL fiscal year produced zero duplicate keys; all 71 SEC-matched AAPL filings would be classified as naming conventions.

   Furthermore, daily_run never refreshes sec_labels. Its inner join silently excludes new filings without a previously loaded SEC label. There is no coverage or freshness failure.

   This contradicts “Nothing is ever auto-accepted.” Real disagreements are also soft, so flow.py:39-40 and cli.py:53-54 still return success. That conflicts operationally with “On 0, nobody has to look.”

   Fix: scope convention exceptions to evidenced companies, periods and direction; require review of new exceptions; refresh labels for new filings or explicitly flag pending verification. Alert on new unreviewed data-quality warnings.

3. “Fetched every filer” does not establish that the fetched data is usable or complete.

   Where: src/sec_filings/loader.py:215-240,293-297; src/sec_filings/checks.py:127-132; docs/decisions.md:97-99.

   Confirmation: an empty recent block is accepted as zero rows; _mark_missing immediately returns when its date array is empty. For BABA, its existing fiscal-year-end override also prevents the missing-FYE check from failing. A valid-JSON response with an empty filings block can therefore count as a successfully fetched filer, preserve existing labels, and leave every hard check unchanged.

   There is also no history-completion marker or comparison between the previous successful run and the new recent-page window. The documented claim that the morning check measures a long scheduling gap is not implemented. Reports that entered and left the window during an outage can be missed silently.

   Fix: validate response identity, required arrays and cardinalities; reject unexpected empty or sharply reduced feeds. Persist history coverage and detect when an outage requires archived-page catch-up.

4. Fiscal labels have reproducible counterexamples; the filing-date fallback is not safe for arbitrary legitimate amendments.

   Where: src/sec_filings/fiscal.py:108-123,127-135,147-152; src/sec_filings/checks.py:270-275; docs/decisions.md:41-45,61-65.

   Confirmation from Python probes:

   - label("10-K", 2027-01-02, "1231", 2027-02-25) returns 2026 FY, method rule. For a 52/53-week year crossing New Year, that contradicts the stated calendar year in which the year actually ends: 2027.
   - A legitimate amendment to FY2023 filed on 2026-05-01, carrying the documented EDGAR quirk reportDate=filingDate, is assigned 2025 FY. The function cannot distinguish which older year was amended from those inputs.

   The second case is especially dangerous: amendments are excluded from uniqueness checks, and rule_filing_date does not itself fail the off-calendar check. Without an SEC label, the wrong attachment can pass unnoticed.

   These are constructed counterexamples, not allegations that those particular filings exist in the loaded table.

   Fix: reconcile the published year convention with cross-year weekly calendars. Do not treat filing-date inference as verified period identification for amendments; use reviewed accession-level corrections or document inspection, and require acknowledgement of new ambiguous fallbacks.

5. Historical fiscal-year-end support promised by D3/D4 does not exist.

   Where: docs/decisions.md:54-55,80; src/sec_filings/loader.py:57-62,343-355; src/sec_filings/flow.py:70-78.

   Confirmation: the schema has no fiscal_year_ends table. Overrides retain one entry per ticker, with later entries replacing earlier ones. relabel_filer explicitly applies the current filer year end to every historical filing.

   A probe of a genuine annual period ending 2026-02-28, filed 2026-03-10, returned 2026 FY with historical FYE 0228 but 2025 FY through the fallback with current FYE 0630.

   Fix: implement effective-dated fiscal calendars and select them by report period, or remove the support claim and hard-stop affected histories for review. A fresh agent currently has no supported way to perform the documented “new validity row” operation.

6. The handover’s missing-ticker recovery cannot clear the failure it describes.

   Where: AGENTS.md:59-63; src/sec_filings/loader.py:77-94,118-126; src/sec_filings/checks.py:146-151,291-292.

   Confirmation: adding extra_ciks creates a company-CIK link but does not resolve the ticker in company_tickers.json. resolve_ciks still puts it in missing; tickers_resolved remains a hard failure. The newly inserted override link is also is_current=false, so the company is excluded from overdue_reports.

   Thus section 4 works for ordinary resolved tickers, but its stated recovery for delisted or otherwise absent tickers leaves the fresh agent stuck—and without deadline monitoring for that manually mapped company.

   Fix: support an evidenced current-CIK override that satisfies resolution and deadline selection, with a distinct warning that the SEC ticker map lacks it.

7. Co-registrants are recorded but not consistently used; upsert fields are last-writer-wins.

   Where: src/sec_filings/loader.py:268-281,302-305; src/sec_filings/sql/002_co_registrants.sql:10-14; src/sec_filings/checks.py:234-239.

   Confirmation: for 0000034088-26-000093, SQL returned primary cik=2115436 and also_filed_by_ciks={34088}. Its two snapshots have different file numbers: 001-43384 for the new CIK and 001-02256 for the old. The stored row keeps the new CIK but the old file number and old-CIK document URL.

   The upsert does not overwrite cik, but it unconditionally overwrites nullable metadata, including report_date and document fields. A sparse later observation can erase previously populated values.

   The reports view and disappearance logic join only the primary CIK. A company linked solely to a secondary registrant cannot see that shared filing. Uniqueness is also enforced per CIK, permitting undocumented conflicting originals across a company’s CIKs.

   Fix: model filing-registrant membership explicitly and use it in reporting and availability checks. Preserve per-registrant fields; reconcile canonical metadata deliberately rather than accepting whichever page runs last. Check cross-CIK conflicts separately from genuine shared accessions.

8. The hard completeness check can pass unaccepted incomplete reports.

   Where: src/sec_filings/checks.py:108-122,211-225.

   Confirmation: labels_complete truncates its findings to 50 before apply_accepted evaluates them. A Python probe with count=51 and the first 50 accepted returned passed=True, severity=info, leaving the 51st report unexamined.

   Separately, the SQL predicate never checks fiscal_period. A SELECT probe with fiscal_year=2026, fiscal_period=NULL, label_method='rule' was not selected as incomplete. A NULL label_method with a populated year also escapes through SQL’s unknown result.

   Fix: evaluate acceptance over the full finding set and truncate only display output. Explicitly require non-null, valid year, period and method.

9. The supposedly statutory deadline calculation is wrong for current supported calendars.

   Where: src/sec_filings/checks.py:27-33,296-322; docs/decisions.md:155-161.

   Confirmation:

   - The recorded BABA/TM next deadline is 2027-07-29 for year end 2027-03-31. The documented four-calendar-month 20-F rule, with weekend rolling, produces 2027-08-02—not 120 days.
   - GME’s recorded next end is 2026-10-30, deadline December 9. Thirteen weeks after its actual August 1 quarter end is October 31, producing December 10.
   - NT handling loads only CIK and report_date. It cannot establish whether the notice was timely, and exact matching against nominal period ends misses weekly-calendar dates. It nevertheless grants grace to every matching notice.

   These are additional errors, not the disclosed federal-holiday approximation.

   Fix: use form-specific calendar-month deadlines, weekly fiscal calendars, and timely, correctly classified notices matched to actual fiscal periods.

Minor findings

10. Adding companies re-fetches every existing company’s history, contrary to the kit.

   Where: AGENTS.md:56-57; src/sec_filings/flow.py:69-71; src/sec_filings/loader.py:242-247.

   Confirmation: backfill=True is passed to every active CIK, and every listed historical page is fetched unconditionally. There is no “new CIKs only once” branch.

   Fix: implement persisted per-CIK backfill completion, with an explicit full-refresh option, or correct the handover’s request-volume and incremental-loading claims.

11. Stored snapshots are not the complete pages promised by D4.

   Where: docs/decisions.md:84-85; src/sec_filings/loader.py:239,313-328.

   Confirmation: the recent snapshot stores a reconstructed object containing only filings.recent, name, fiscalYearEnd and category. SQL confirmed those four top-level keys. It omits filings.files, formerNames and other metadata, although its hash is calculated from the full raw response.

   This prevents reproducing the hashed page and some of the historical investigation the kit promises. The unique (cik,page,sha256) constraint also cannot record an A→B→A observation sequence as successive snapshots.

   Fix: store the complete parsed response and record each run’s observation of its content hash.

12. The promised SPAC-history note is absent.

   Where: docs/decisions.md:31-32; src/sec_filings/loader.py:343-362.

   Confirmation: SQL for LCID’s FY2020 original returned no company note or filing label_note; its CIK note is only the ticker-map title. The report output provides no pre-merger marker. Churchill history exists only in filer former_names and the decision log.

   Fix: expose period-appropriate predecessor/SPAC provenance with reports, or remove the claim that these filings are labelled with a note.

13. Remaining misleading interface and configuration claims.

   - docs/decisions.md:119-120 lists add-company. The CLI help and cli.py:19-32 contain no such command. Remove the claim or implement it.
   - docs/decisions.md:128-129 says check reruns checks. cli.py:83-101 only reloads stored results. Correct that distinction; configuration or database changes are not re-evaluated.
   - README.md:59-60 and D1 omit supported 10-KSB, 10-KT405, 10-QSB and NT 10-K405 forms. fiscal.py:17-20 and a set-difference probe confirmed them. Document the actual ingestion scope.
   - docs/decisions.md:24 describes companies(ticker,name), but companies has no name column. Names live on filers. Correct the schema description.
   - fiscal.py:101-105 accepts 0231, and fye_date silently converts it to February 28. The documented special clipping case is February 29, not arbitrary impossible dates. Validate actual calendar dates.
   - AGENTS.md:25 points to nonexistent section 5.3. Point to the actual verification explanation.
   - D6 promises retries on 5xx generally; edgar.py:22 retries only 500, 502, 503 and 504. State the actual policy or broaden it.
   - AGENTS.md:71 promises naming-convention counts in the printed check. With accepted anomalies, apply_accepted replaces that summary. The latest log prints only “4 finding(s) accepted”; SQL shows 101 convention differences and 105 raw verify-labels disagreements. Preserve those counts and explain the raw verification exit status.

Accepted limitations

- Excluding 6-K interim disclosures is explicit. The live summary returns zero quarterly reports for the five documented foreign private issuers.
- Choosing a normalized fiscal-year convention rather than issuer naming is legitimate; the cross-year implementation defect and unrestricted exceptions above are separate issues.
- Pre-XBRL history cannot receive the same companyfacts cross-check. That coverage limitation is stated.
- Federal holidays and filing-content changes are explicitly outside the implemented checks.
- Sequential replays need not leave bookkeeping untouched: runs 2 and 3 each inserted zero filings and updated 558 observations. Accession-key deduplication and retaining the first CIK are implemented; that does not resolve the alert-state and metadata problems above.
- Default single-client fair access includes retries. A no-network transport probe recorded 12 attempts, two retries, minimum spacing 0.2 seconds and at most five attempts per half-open second. User-Agent and Accept-Encoding headers were present.

Verification: check, report AAPL 2009 FY and summary each exited 0 after setting DATABASE_URL. The Apple query returned the documented original and one amendment. No pytest, EDGAR requests, file edits or database mutations were performed.
