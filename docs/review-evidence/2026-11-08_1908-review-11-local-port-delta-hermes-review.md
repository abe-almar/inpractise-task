# Hermes reviewer report: review-11-local-port-delta

- Generated: 2026-11-08 19:08 CET
- Profile: takehomereviewer (gpt-6.1-sol, reasoning max, report-only)
- Brief: /Users/homerolapida/.hermes/briefs/takehome/review-11-local-port-delta.md
- Every finding below still has to be checked against the code and data before it is acted on.

VERDICT: PASS WITH FINDINGS

Minor — Pass-10 Minor 1 is only partly closed
AGENTS.md:120-123; src/sec_filings/db.py:38-39; docs/decisions.md:140

The recovery instruction still says DB_PORT=<free port> make run moves the database without qualifying when that works. With an existing DATABASE_URL or .env, it neither changes the job’s URL nor starts Compose. Following the displayed remedy can therefore retry the same occupied endpoint.

Confirmed with GNU Make 3.81 dry-runs and a read-only exported-environment probe: with DATABASE_URL pointing to localhost:25434 and DB_PORT=5433, the job still receives port 25434 and run does not invoke Docker. A simulated connection refusal confirmed that the actual CLI nevertheless prints the unqualified remedy.

D8 also needs a precedence qualification: an inherited DB_PORT wins over Compose’s .env value. In-memory .env probes confirmed that changing shell DB_PORT does not change the job’s separately configured URL.

Fix: qualify the error hint and AGENTS section 5 with “when make supplies the database.” For configured local installations, instruct the operator to change DATABASE_URL and DB_PORT together, then run make db before make run. Clarify shell precedence in D8.

Closure checks

  Major 1 — Closed. On GNU Make 3.81, override plus export gives both child processes the same port. Unset, empty-environment and empty-command-line values select 25432; nonempty environment and command-line values are preserved. Explicit make db also retains the intended behavior.

  Major 2 — Closed for the stated policy. A simulated driver echo of a percent-decoded password was masked, including in the rendered traceback. Actual malformed-DSN parser failures produced the fixed diagnostic; the CLI returned 2. No realistic encoded-credential or complete-URL disclosure was demonstrated, so the earlier simulated case is not retained as a finding. from None suppresses the displayed context, but the first-line reason remains and the underlying exception is still inspectable through __context__.

  Minor 2 — Closed. Pure formatter probes confirmed default host/default port, hostaddr fallback, and single-line whitespace normalization.

  Minor 1 — The Makefile behavior is fixed. README:22-25, AGENTS section 2 and .env.example describe the configuration split consistently. The remaining recovery guidance is identified above.

Accepted limitations and verification boundaries

  No new runtime defect was confirmed. With neither configuration source present, the default job port and Compose binding are 25432, so another Postgres on 5432 does not create the original collision.

  GNU Make 4.4.1 compatibility is supported by docs/evidence/make-run-clean-clone-2026-11-08.log:283-291, not a local rerun. A clean Linux Compose-first run was not independently exercised.

  .env scenarios and driver-echo/refusal cases were simulated in memory. Malformed-DSN parser failures were real. No ingestion, suites, container changes or file writes were performed; the working-tree status remained unchanged.
