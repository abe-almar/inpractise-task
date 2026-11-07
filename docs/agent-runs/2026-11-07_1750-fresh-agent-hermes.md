# Hermes fresh-agent run (no persona, no memory: --ignore-rules)

- Generated: 2026-11-07 17:50 CET
- Profile: takehome model settings only (gpt-6.1-sol, reasoning max; subagents xhigh); worktree /Users/homerolapida/Desktop/take-home-fresh
- Brief: /Users/homerolapida/.hermes/briefs/takehome/fresh-agent-add-companies.md

VERDICT: BLOCKED on Disney fiscal labelling. The companies and predecessor histories are loaded, but the kit’s definition of “done” is not met.

The complete report, in your requested order, including unabridged verbatim output from the final run and every completion check, is:
 /Users/homerolapida/Desktop/take-home-fresh/docs/evidence/extension-2026-11-07-report.txt

1. Companies

Counts are original reports; amendments are stated separately.

GOOGL: CIKs 1652044 and 1288776; fiscal year end 1231; 22 annual and 69 quarterly reports, plus 3 annual and 2 quarterly amendments. Added predecessor Google Inc., valid_to 2015-10-02, to recover the earlier history.

NVDA: CIK 1045810; nominal fiscal year end 0131, allowing its 52/53-week calendar; 28 annual and 83 quarterly reports, plus 3 annual and 2 quarterly amendments. Added two cover-verified companyfacts label exceptions; no fiscal-year-end override.

DIS: CIKs 1744489, 1001039 and 29082; respective fiscal year ends 1003, 0928 and 0930; 33 annual and 99 quarterly reports, plus 19 annual and 4 quarterly amendments. Added both predecessors, valid_to 2019-03-20 and 1996-02-09, and three cover-verified companyfacts label exceptions. A separate wrong-year metadata problem remains unresolved.

BP: CIK 313807; fiscal year end 1231; 28 annual and 0 quarterly reports, plus 3 annual amendments. No new overrides. Its listed submissions contain 20-Fs and 6-Ks, not quarterly 10-Qs.

SONY: CIK 313838; fiscal year end 0331; 31 annual and 0 quarterly reports, plus 1 annual amendment. No new overrides. Its listed submissions contain 20-Fs and 6-Ks, not quarterly 10-Qs.

Evidence: docs/evidence/extension-2026-11-07-audit.sql and .log. Independent accession-set reconciliation found no missing or unexpected tracked filings for any of these eight CIKs.

2. Final run and completion checks

The final run’s check-summary lines and RESULT, copied verbatim:

    run 5 (daily): status=ok filers=29/29 filings seen=709 new=0 relabelled=0 requests=30 errors=0 duration=6s
    [ok  ] run_complete: fetched 29 of 29 filer pages
    [ok  ] http_health: 0 EDGAR errors, 0 retries, 6s of a 600s budget
    [ok  ] tickers_resolved: every ticker is in company_tickers.json
    [ok  ] cik_changes_acknowledged: no ticker changed CIK
    [ok  ] fiscal_year_end_known: every filer has a fiscal year end
    [ok  ] no_filings_lost: no filing disappeared from the feed (0 older disappearances on record)
    [ok  ] labels_complete: every report has a fiscal label
    [FAIL] labels_unique: 1 (company, year, period) with more than one original report; look at each
            - {'key': 'DIS/1001039/quarterly/2008/Q2', 'accessions': ['0001193125-08-104229', '0001193125-09-100081'], 'forms': ['10-Q', '10-Q']}
    [WARN] labels_far_from_calendar: 30 filing(s) not labelled straight from the period of report: 25 from the filing date (EDGAR quirk), 5 off the fiscal calendar (best effort)
    [ok  ] overdue_reports: no company is past its filing deadline
    [info] labels_match_sec: 9 finding(s) accepted in overrides.toml
    [info] news: 0 new filing(s) this run, of which 0 amendment(s) or late-filing notice(s)
    RESULT: ATTENTION NEEDED

Exit code: 1. The complete output, including the warning-item detail, is in the report above and docs/evidence/extension-2026-11-07-final-run.log.

Completion results:

    $ .venv/bin/pytest -q
    ........................................................................ [ 32%]
    ........................................................................ [ 64%]
    ........................................................................ [ 96%]
    ...s...                                                                  [100%]
    222 passed, 1 skipped in 0.14s
    exit_code=0

    $ TEST_DATABASE_URL=$DATABASE_URL .venv/bin/pytest -q tests/test_replay_db.py
    .                                                                        [100%]
    1 passed in 0.27s
    exit_code=0

The exact “run && run” command exited 1 on its first run, so its right-hand command did not execute. I explicitly ran another daily attempt afterward: both returned new=0, and both failed the same uniqueness check.

The Apple query passed: one 10-K, accession 0001193125-09-214859, filed 2009-10-27, with one amendment. Summary returned all 25 companies with annual originals. The morning check also exited 1, retaining the Disney failure. Full verbatim outputs—including the complete summary and git status—are in the report, without elision.

The final database readback confirmed:

    {"id": 5, "mode": "daily", "status": "ok", "checks_passed": false, "ciks_expected": 29, "ciks_fetched": 29, "requests": 30, "http_errors": 0, "filings_inserted": 0}

3. Where the kit was wrong, unclear or incomplete

Exact proposed replacements/additions, with supporting checks, are recorded in:
 /Users/homerolapida/Desktop/take-home-fresh/docs/extension-handoff.md

The most consequential changes are:

    Replace the claim that backfill fetches only new CIK histories:
    “Backfill rereads the recent page and every listed history page for every configured CIK, including existing companies; only run limits ingestion to recent pages.”

    Add after the predecessor instruction:
    “Investigate predecessor chains recursively, including older 8-B12B/8-B12G succession registrations; after adding any predecessor CIK, rerun sec-filings backfill before verify-labels or run.”

    Replace the duplicate-label remedy:
    “Compare each cover's actual covered period with submissions reportDate before accepting a duplicate: accept only genuine multiple originals for the same covered period; if metadata puts different years in the same label, the current kit has no per-filing correction override, so retain the failure and report the blocker rather than accepting a wrong label or editing rows by hand.”

    Add to the SEC-label comparison instruction:
    “A one-year difference with the same period is automatically classified as naming convention by the current check, but that is only a heuristic: inspect the filing before claiming it is the issuer's convention.”

    Add to the cover-page check:
    “If primary_document_url returns 404 for an old filing, open that accession's filing index and use its actual document link or complete submission text; do not guess a replacement path or assume the filing is missing.”

The handoff also records the nonexistent “section 5.3” reference, unsupported historical FYE validity rows, employee-plan amendments appearing under issuer CIKs, and stale fixed company/request counts.

4. What I could not finish and why

Disney accession 0001193125-09-100081 was filed on 2009-05-05. Submissions says reportDate 2008-03-28, but the actual cover says March 28, 2009.

Consequently, the job labels it 2008 Q2, collides with the genuine 2008 Q2 filing, and returns no DIS 2009 Q2 answer. Accepting that collision would silence the check without correcting the answer. Changing the fiscal year end cannot repair a wrong report year.

The documented overrides support extra CIKs, fiscal year ends and accepted findings—not per-accession metadata corrections. I stopped at that limitation rather than invent an override or hand-edit database rows. A follow-up implementation needs to preserve the raw SEC date and apply evidence-backed corrections during every relabel.

Four older Disney amendments also contain employee savings-plan statements rather than issuer annual financial statements; their classification and attachment need explicit handling.

Changes are left uncommitted in companies.csv, overrides.toml, tests/test_fiscal.py, docs/decisions.md and the new evidence/handoff files. No production code or schema was changed. Nothing was committed or pushed.
