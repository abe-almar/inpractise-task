# Handover: running, extending and fixing the SEC filings job

Work from the repository root. [README.md](README.md) explains the schema, queries and deployment;
this file covers maintenance. Keep company-specific facts and their source URLs in `overrides.toml`.

## 1. What the job guarantees

`make run` loads EDGAR submissions into Postgres, labels annual and quarterly reports with their fiscal
year and period, and checks the result. It exits 0 only when the run completed and all hard checks passed.
Soft warnings still need review. The `reports` view selects one original per company/year/period and
lists its amendments and other originals; the underlying `filings` table retains every tracked accession.

The scheduler owns missed-start, timeout and failure notifications. Schedule `make run` daily, retain its
output in the scheduler, and use [the morning check](docs/morning-check.md) to review the result.

## 2. Set up and run

Use Python 3.12+, `make`, and Docker Compose, or supply an existing Postgres database.

```bash
make test
make run
make run
make report TICKER=AAPL YEAR=2009 PERIOD=FY
make verify-labels
```

The first run reads each new filer's full history. A repeat run with unchanged SEC data reports `new=0`.
Apple's example returns one 10-K, filed 2009-10-27, with one amendment. `make help` lists the commands;
`make docker-run` runs the job in a container.

With neither `DATABASE_URL` nor `.env`, `make` starts the compose database on localhost port 25432.
`DB_PORT=5433 make run` moves that managed database's port without discarding its data. Otherwise the job
uses the configured URL; `make db` starts the compose database manually. Creating `.env` disables the
automatic database configuration, so include `DATABASE_URL` in it. Set `SEC_USER_AGENT` to identify the
operator and a contact email. Keep the default rate of five requests per second.

Read the `database: host:port/name` banner before trusting the result. `report` and `summary` print it to
stderr so their JSON stays clean. If a moved repository leaves `.venv` unusable, remove that virtualenv
and let `make` rebuild it. Update pinned dependencies with `make lock`, then use the completion checks.

## 3. Data rules to preserve

- **Forms and amendments.** Track the 10-K and 10-Q families, 20-F and 40-F, their amendments and supported
  late notices. Foreign private issuers generally publish interim information as 6-Ks, which are outside
  this job's scope; do not manufacture quarterly rows. Exhibit-only and employee-plan 10-K/A filings
  remain amendments. An amendment does not, by itself, mean the original financial statements were wrong.
- **Companies and registrants.** A company can have several CIKs over time. Preserve predecessor CIKs in
  `company_ciks`, configured through `[[extra_ciks]]`. Store a shared accession once, retain co-registrants
  in `also_filed_by_ciks`, and keep the first registrant's metadata. Company queries must include those
  co-registrants. A predecessor's `valid_to` records succession; it does not truncate its filing history.
- **Fiscal labels.** `fiscal_year` is the calendar year of the nominal fiscal year end nearest the period
  end; quarters count from the preceding nominal year end. The rule allows ten days for 52/53-week calendars.
  Issuers' start-year naming belongs in `[[naming_conventions]]`; keep the SEC label beside the computed
  label. A one-year discrepancy alone does not prove a naming convention. Correct an erroneous EDGAR period
  per accession under `[[filing_corrections]]`, preserving the original `report_date` and using
  `report_date_corrected`. Use dated `[[fiscal_year_ends]]` entries when the calendar changed. Do not repair
  individual records with direct SQL or distort the general rule to fit one filing.
- **Inference and verification.** Old filings sometimes carry their filing date as the report period.
  Inferred amendment labels require cover-page review. Companyfacts provides a separate comparison; it is not the
  source of our labels: non-XBRL filings cannot be cross-checked there, missing XBRL labels have a 21-day
  grace period, and existing labels can be cached. `make verify-labels` explicitly refreshes all filers.
  Accept only a finding whose cause has been checked, using its exact check/key and a source explanation in
  `[[accepted_findings]]`. Acceptances have no automatic expiry; revisit them when their assumptions change.
- **History and retention.** New CIKs always receive a full load. Daily runs normally read recent pages,
  checking relevant history pages when tracked filings roll out of the recent feed. A confirmed missing
  filing gets `not_seen_since` and remains a hard finding until it reappears or is explicitly accepted.
  Rows are never deleted. `make backfill` repairs recorded coverage gaps; `make backfill FULL=1` re-reads
  all history, including changes outside the daily window. Selective rollover reads do not clear a gap.
- **Transactions and time.** Preserve the session advisory lock around the entire run so overlapping jobs
  cannot compare older responses with newer writes. Updates commit per filer: a failed run can leave
  refreshed rows queryable, so downstream consumers must check the overall run verdict. Store timestamps
  in UTC; `make check` uses the UTC date. SEC filing/report dates remain dates, without timezone conversion.
- **Deadline limits.** Overdue checks are warnings. They model filer-category deadlines, weekend rollover
  and timely late-notice extensions, but not federal holidays. Form 20-F uses four calendar months;
  Form 40-F requires a Canadian filing calendar and produces an explicit unsupported-deadline warning.
  Confirm a warning against the filing index and the issuer's applicable rules.

## 4. Add or remove companies

1. Append tickers to `companies.csv`, keeping its `ticker` header, then run `make run`. Matching is
   case-insensitive. If a ticker is absent from the SEC ticker map, establish its CIK using SEC company
   search and add a sourced `[[extra_ciks]]` entry with `current = true`.
2. Investigate predecessor registrants before trusting completeness. Look for recent first filings at an
   old business, `formerNames`, and successor notices such as `8-K12B`, `8-K12G3`, `8-B12B` or `8-B12G`.
   Follow the chain recursively; an acquired business is not automatically a predecessor. Add verified
   CIKs under `[[extra_ciks]]` with `valid_to`, a note and a source URL, then rerun to load their histories.
3. Resolve findings from actual documents. Missing `fiscalYearEnd` needs an annual cover and a
   `[[fiscal_year_ends]]` entry with `mmdd`. Duplicate originals need both covers: correct wrong periods,
   or accept a genuine transition/re-filed original. For an inferred amendment, confirm the covered period
   before accepting it. If an old primary-document URL fails, open the accession's filing index and follow
   its complete-submission link.
4. Run `make verify-labels`, then `make run`. Investigate every `labels_match_sec` item. An explicit issuer
   naming convention can justify `sec_year_offset = -1`; use `until` for the last affected filing date when
   needed. Do not infer a convention from offset counts, or claim a naming change merely because SEC tags
   changed. If the SEC label itself is wrong, accept the exact accession with the cover-page explanation.
   For `no_sec_label`, inspect all companyfacts facts for the accession before accepting absence.
5. For each added company, run `make report TICKER=X YEAR=Y PERIOD=FY` for a chosen year and compare its
   document's covered period with the label and corrected period, if present. Check the earliest history
   and predecessor coverage too. Add a fiscal test if the calendar pattern is new, then run section 7.

Removing a ticker makes the company inactive and retains its filings. `labels_match_sec` still checks
inactive companies, so removing one does not clear its unresolved findings.

## 5. Diagnose a failed morning check

Read the failing items, not only the totals. Most repairs are overrides followed by an idempotent rerun.

| Check | Action |
|---|---|
| `run_complete` | Inspect the named fetch/validation error and rerun. Retry logs distinguish HTTP failures from invalid responses. |
| `tickers_resolved`, `fiscal_year_end_known` | Resolve the ticker or fiscal calendar as in section 4. |
| `cik_changes_acknowledged` | Compare old/new submissions, confirm succession, and record the old CIK in `[[extra_ciks]]`. Both continue to be fetched meanwhile. |
| `history_gap` | Run `make backfill`; the gap clears only after a successful history read. Use `FULL=1` when all history needs checking. |
| `sec_labels_refreshed` | The failed companyfacts refresh retries on the next run; investigate persistent failures. |
| `no_filings_lost` | Inspect the filing index and history. Accept only a verified removal; reappearance clears the flag automatically. |
| `labels_complete` | Supply a cover-supported report-period correction or fiscal year end, according to `no_report_date` or `no_fye`. |
| `labels_unique`, `labels_inferred_for_amendments`, `labels_match_sec` | Follow the document checks in section 4. |
| `labels_unique_across_ciks` (soft) | Check for a reorganisation or wrong label. The report view prefers the current CIK and lists other originals. |
| `overdue_reports` (soft) | Check the issuer's index, late notices and deadline/calendar limitations in section 3. |
| `labels_far_from_calendar` (soft) | Inspect new items for an unusual calendar or a bad EDGAR report date. |

`make check` reprints stored checks; it does not fetch or re-evaluate data. It succeeds only if the latest
run is clean and started today in UTC, and warns when the input-file hashes have changed. Run `make run` to
evaluate current inputs. A successful process exit still permits soft warnings.

A connection failure can happen before a `runs` row exists. Test the published port from the host;
Docker's internal health check alone is insufficient. For a port conflict, change `DB_PORT` when using
the managed database, or change both the explicit URL and compose port. If the published port remains
unreachable after resolving the conflict, recreate the service with `docker compose up -d --force-recreate db`.

## 6. Change the implementation

The pure `label()` function is in `src/sec_filings/fiscal.py`. Add a failing test with the real dates before
changing its rule. Runs relabel fetched filers with the current rule; inspect `runs.notes->>'relabelled'`
and compare SEC disagreements before and after. A new disagreement needs investigation, not acceptance
merely to make the check green.

Add database changes as new numbered files in `src/sec_filings/sql/`; applied migrations run once, so
editing an existing migration does not upgrade an existing database. Exercise persistence, recovery and
concurrency changes with the Postgres tests. Keep new source-backed exceptions in `overrides.toml` and
update README or this handover when behavior changes.

## 7. Completion checks

For implementation, dependency or company/override changes:

```bash
make test
make test-db
make run
make run
make verify-labels
make report TICKER=AAPL YEAR=2009 PERIOD=FY
make summary
git status --short
```

`make test-db` uses disposable schemas. With a custom database, export `DATABASE_URL`; that target does
not read it from `.env`. Expect both live runs to pass, and the second to add nothing unless SEC data
changed between them. Verify that the Apple example remains correct, every active company has annual
coverage, and quarterly coverage matches its filing regime. Review soft warnings and every changed
override, rather than relying solely on exit codes. For documentation-only changes, check the diff,
commands and links; live ingestion is unnecessary.
