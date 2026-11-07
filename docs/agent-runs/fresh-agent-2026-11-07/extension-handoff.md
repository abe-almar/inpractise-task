Handover-kit corrections identified during the five-company extension, 2026-11-07

These are proposed exact replacement/addition sentences. AGENTS.md was deliberately left unchanged so the tested kit remains identifiable. Evidence and decisions are in docs/decisions.md D15-D16 and docs/evidence/extension-2026-11-07-*.

1. AGENTS.md section 4, step 2 currently says: "It fetches every history page of the new CIKs only once; existing companies are refreshed from their recent page."
   Replace with: "Backfill rereads the recent page and every listed history page for every configured CIK, including existing companies; only run limits ingestion to recent pages."
   Check: src/sec_filings/flow.py:69-71, loader.py:242-247, and the two backfill stderr logs (135 requests on the predecessor backfill).

2. Add to section 4, step 4:
   "Investigate predecessor chains recursively, including older 8-B12B/8-B12G succession registrations; after adding any predecessor CIK, rerun sec-filings backfill before verify-labels or run."
   Check: DIS requires both 1001039 and 29082, not just the current 1744489. Its 1995 8-B and joint 1996 8-K identify the earlier lineage. Google needs 1288776 and three older pages.

3. Replace the labels_unique remedy in section 4, step 3 with:
   "Compare each cover's actual covered period with submissions reportDate before accepting a duplicate: accept only genuine multiple originals for the same covered period; if metadata puts different years in the same label, the current kit has no per-filing correction override, so retain the failure and report the blocker rather than accepting a wrong label or editing rows by hand."
   Check: 0001193125-09-100081 covers March 28, 2009, but the API says 2008-03-28. The current reports view omits DIS 2009 Q2. See extension-2026-11-07-audit.sql and disney-findings-covers.log.

4. Add to section 4, step 5:
   "A one-year difference with the same period is automatically classified as naming convention by the current check, but that is only a heuristic: inspect the filing before claiming it is the issuer's convention."
   Check: NVIDIA's 0001045810-11-000015 says FISCAL YEAR 2011 and year ended January 30, 2011, whereas companyfacts labels it 2010 FY. The heuristic suppresses it from the actionable list. See sec-disagreement-covers.log.

5. Replace "exit 1 is normal, see 5.3" in setup with:
   "Exit 1 means raw labels differ, even when differences have been accepted; inspect verify-labels output and then the labels_match_sec morning-check detail in section 5."
   There is no section 5.3. The final cross-check stored/compared 1254 labels, found 140 raw differences, and skipped DIS/29082 (no companyfacts). Accepted findings do not change this command's exit code.

6. Add to the cover-page spot-check instruction:
   "If primary_document_url returns 404 for an old filing, open that accession's filing index and use its actual document link or complete submission text; do not guess a replacement path or assume the filing is missing."
   Check: 0001001039-00-000012's generated /0001.txt URL failed, but its index links /0001001039-00-000012-0001.txt, which was fetched successfully.

7. Add to the labels_far_from_calendar remedy:
   "A 10-K/A under an issuer CIK can contain employee savings-plan financial statements with a different year end; inspect the document and do not change the issuer FYE to fit the plan, or assume that a form match proves an issuer financial correction."
   Check: four Disney amendments are savings-plan statements. 0001001039-99-000015 also has a 1998 plan cover versus 1997 metadata. They remain loaded under the form-based policy; plan/issuer separation needs implementation, not blanket acceptance.

8. Replace docs/decisions.md D3's statement "a change of fiscal year end is recorded as a new validity row for the company" with:
   "The current implementation uses one effective fiscal year end per CIK for all its filings; historical date-valid FYE changes are not implemented, and repeated fiscal_year_ends entries for the same ticker overwrite one another."
   Check: loader.py:57-64,343-355 and sql/001_init.sql:53-68. No such override was needed for these five.

9. Replace fixed company/request counts in the overview with:
   "Company counts and request counts depend on companies.csv and all configured predecessor CIKs; inspect the actual run instead of expecting the original twenty-company counts."
   Check: this extension has 25 active companies, 29 CIKs and 30 requests per daily run. BP and SONY join the original five annual-only foreign filers.

Scope of the stop

The documented extension path is blocked, not the SEC network or database. Fixing it requires a reviewed per-accession metadata/label correction mechanism that preserves raw submissions values and participates in every relabel. A labels_unique acceptance alone would hide DIS 2009 Q2 rather than repair it. No production code/schema change or speculative override was made. The separate employee-plan classification limitation is also left explicit for the next implementation task.
