# Hermes reviewer report: review-10-local-port

- Generated: 2026-11-08 18:52 CET
- Profile: takehomereviewer (gpt-6.1-sol, reasoning max, report-only)
- Brief: /Users/homerolapida/.hermes/briefs/takehome/review-10-local-port.md
- Every finding below still has to be checked against the code and data before it is acted on.

VERDICT: FAIL

Major 1 — Makefile:8-10; docker-compose.yml:11
An empty DB_PORT makes Compose and the job select different ports.

Confirmed with GNU Make 3.81 dry-runs: DB_PORT="" produces DATABASE_URL ending in localhost:/sec_filings. The real libpq parser treats that as having no port; its compiled default is 5432. Compose’s ${DB_PORT:-25432} instead selects 25432.

This reintroduces a route to the unrelated Postgres the change is meant to avoid. The endpoint mismatch is confirmed; a successful ingestion against the wrong database was not exercised.

Fix: reject an empty/invalid DB_PORT, or normalize it consistently before constructing either configuration. Add empty-environment and empty-command-line cases; tests/test_cli.py:36-41 checks only the literal default strings.

Major 2 — src/sec_filings/db.py:28-32; tests/test_cli.py:23-33
The “never carries the password” guarantee is not implemented. The first driver-error line is copied verbatim into the printable exception.

Confirmed by fault injection: an OperationalError whose first line contains a dummy password makes cli.main(["summary"]) return 2 and print that password. This was a simulated driver payload, not a demonstrated real-server password disclosure. A password-free URI rendering does not protect against credentials appearing in the driver’s own message.

The new test exercises connection refusal, where the driver already omits the password. It does not establish sanitization.

Fix: omit arbitrary driver text from the public diagnostic, or use a defensible sanitization/allowlisting policy. Add explicit first-line-secret cases, including encoded credentials and complete connection strings.

Minor 1 — Makefile:8; README.md:22-25; AGENTS.md:26-28
DB_PORT in .env has different behavior under hand-run Compose and make db, without guidance explaining the distinction.

For a .env containing DB_PORT=5433 and a matching DATABASE_URL, hand-run Compose can select 5433. Make unconditionally exports its default 25432, overriding Compose’s .env interpolation, while the job still uses the supplied URL’s 5433. Likewise, DB_PORT=5433 make run does not rewrite an existing DATABASE_URL or automatically start Compose when .env exists.

Confirmed through actual Make dry-runs and in-memory .env probes; no container was moved during this review.

Fix: document the supported precedence and the paired URL/startup steps for configured installations. Either support .env-only DB_PORT without introducing another conflicting reader, or explicitly require it in the shell environment.

Minor 2 — src/sec_filings/db.py:26-28
The endpoint formatter can name the wrong endpoint and violate the one-line contract.

Confirmed:
  - With synthetic PGHOST/PGPORT defaults, real psycopg parameter lookup selected remote.example.invalid:25435, while an injected connection failure was reported as localhost:5432. An explicit hostaddr without host was also reported as localhost.
  - A guarded actual CLI/psycopg preflight probe using a percent-encoded newline in host produced two stderr lines. DNS and connection attempts were blocked and never reached; the validation error itself was real.

Fix: do not invent localhost:5432 when connection defaults are unspecified. Account for hostaddr/defaults, or describe them as unspecified. Escape control characters in rendered endpoint values.

Accepted limitations and verification boundaries

  - All six registered CLI subcommands use the shared connection path. Simulated initial failures returned 2. A direct daily_run caller receives DatabaseUnavailable, with OperationalError retained as its cause; the supplied scheduler wrapper still fails rather than returning success. Type-specific OperationalError retry policies would need updating.
  - A simulated connection loss after opening was not rewritten as “the run never started”: OperationalError escaped and the connection was closed. Mid-run tracebacks remain outside this initial-connection handler.
  - Malformed connection strings remain a pre-existing boundary: guarded actual parser probes produced an uncaught ProgrammingError containing a malformed password token, and UnicodeDecodeError for invalid UTF-8. These occurred before message construction. No realistic failure of the second parse after a successfully parsed DSN was confirmed.
  - 25432 is a reasonable fixed default, not a guarantee of availability. This host’s reported outgoing-port range is 49152–65535. The supplied log supports port movement with retained data and local bind-failure recovery; the force-recreate remedy remains explicitly second-machine evidence, not a local reproduction.
  - GNU Make 3.81 was exercised directly. GNU Make 4.x, ingestion, installation and suite results remain supported by the supplied evidence, not rerun here. No files were written.
