# Hermes reviewer report: review-6-refactor

- Generated: 2026-11-07 22:09 CET
- Profile: takehomereviewer (gpt-6.1-sol, reasoning max, report-only)
- Brief: /Users/homerolapida/.hermes/briefs/takehome/review-6-refactor.md
- Every finding below still has to be checked against the code and data before it is acted on.

VERDICT: PASS WITH FINDINGS

Findings

MINOR — docs/decisions.md:47–49: The rewrite drops material conditions from the filing-date inference rule.

    It now describes equality between report date and filing date, followed by selection of the last preceding nominal period end. The implementation instead requires an off-calendar label, dates within 14 days of each other, and a candidate period end at least 14 days before filing.

    Confirmed against the previous D3 wording and src/sec_filings/fiscal.py:112–139. Pure-function probes showed:
    - Report date 2026-02-20, filing date 2026-02-27, form 10-K, year end 1231: rule_filing_date, FY2025, despite unequal dates.
    - Report date and filing date both 2026-04-05, form 10-Q, year end 1231: rule, not rule_filing_date, because the period is within calendar tolerance.

    Fix: restore the off-calendar condition and both 14-day thresholds. This is a documentation regression, not a confirmed change to stored labels.

Runtime-equivalence evidence

    Upsert — src/sec_filings/loader.py:316–368:
    INSERT column/value mappings matched. SELECT-only evaluation of the actual old/new SET expressions produced zero differences across 90 scenarios covering owner/co-registrant paths, NULL preservation, document/URL coupling, false/zero values, repeated co-registrants and disappearance flags. Five fake-cursor cases produced identical inserted/updated counts.

    Schema — src/sec_filings/sql/001_schema.sql:
    Compared all seven original migrations: nine tables, 106 column definitions, table constraints, two explicit indexes and the final reports view matched by name and definition. Evaluating the old final view against the loaded database produced zero symmetric row differences from reports.

    Checks — src/sec_filings/checks.py:
    All sixteen raw CheckResults matched on loaded data, including severity, pass condition, summary and detail. Acceptance handling and formatted output also matched. Synthetic failure and NT-deadline cases matched. The extracted old calendar block and _next_expected_period agreed on 29,280 inputs.

    Flow and ingestion — src/sec_filings/flow.py; src/sec_filings/loader.py:
    Twenty-four in-memory orchestration scenarios matched step order, rollback behaviour, refresh-attempt stamping, partial-fetch handling and RunFacts inputs after removing the two dead fields. Run-closing SQL and values matched for successful, hard-check-failed and failed executions. Ingestion matched across 128 scenarios; coverage SQL matched across 24. All 117 stored submissions pages parsed identically.

    No runtime regression was confirmed. The loaded database corroborated the rebuild evidence: 1,800 filings, runs 1–3 with sixteen checks each and no hard failures, and the shared Exxon filing retaining its owner’s metadata with one co-registrant.

Harder to understand

    src/sec_filings/loader.py:36:
    The walrus-expression comprehension combines cleaning, filtering and deduplication that the previous loop expressed separately.

    src/sec_filings/loader.py:260,271:
    Conditional min(generator) expressions make the disappearance-window boundary less explicit than the previous comparisons. Named intermediate values would expose the boundary cases more directly.

Accepted limitations

    Upsert DML was not executed. Verification used SELECT evaluation of SQL expressions, in-memory control-flow probes and the existing rebuilt database; concurrent transactions were not exercised.

    Physical column order changed in filers, filings and source_snapshots. Named definitions and view results match, but ordinal interfaces such as SELECT * exports are not identical.

    No pytest, run, backfill or verify-labels command was executed. No repository files were changed.
