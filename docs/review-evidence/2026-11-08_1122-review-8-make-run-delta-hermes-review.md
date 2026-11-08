# Hermes reviewer report: review-8-make-run-delta

- Generated: 2026-11-08 11:22 CET
- Profile: takehomereviewer (gpt-6.1-sol, reasoning max, report-only)
- Brief: /Users/homerolapida/.hermes/briefs/takehome/review-8-make-run-delta.md
- Every finding below still has to be checked against the code and data before it is acted on.

VERDICT: FAIL

MAJOR — Makefile:7-10; src/sec_filings/settings.py:21-30
F2 remains open: the presence check can still select the wrong database.

    Confirmed with in-memory .env inputs, the actual load_dotenv function, and Make dry-runs:
    - A leading tab before DATABASE_URL is accepted by load_dotenv but missed by grep.
    - “export <tab>DATABASE_URL=…” is likewise accepted by the reader but missed by grep.
    - In both cases Make exports the local default and selects Docker. load_dotenv’s setdefault then cannot restore the intended remote URL.
    - DATABASE_URL_OLD=… has the opposite detection error: grep suppresses the default and Docker, but the reader supplies no DATABASE_URL. This fails configuration rather than silently choosing another database.

    The original spaces and unquoted-& probes now work. Plain “export<tab>DATABASE_URL”, a lower-case key, and a BOM-prefixed key are not recognized as DATABASE_URL by either reader; those are not parser disagreements.

    Fix: share the exact key-recognition logic instead of maintaining a second grammar in grep. Add regression cases for leading whitespace, mixed export whitespace, and similarly prefixed keys.

MAJOR — src/sec_filings/loader.py:213-220,289-305
F1 is partially closed: equal counts do not establish that the advertised history partition was read.

    All 96 stored history pages passed the new validator. Empty/truncated responses are rejected, and the midway-failure probe reached neither filing upsert nor coverage advancement.

    However, using actual stored JPM responses, validate_history_page accepted submissions-024.json for submissions-023.json. Both contain 2,024 filings, but the advertised start is 2023-07-28 and the replacement starts 2023-06-13.

    A full in-memory replay of JPM’s real recent response and all 71 advertised history requests, returning page 024 again for page 023, reached commit and marked history complete. Unique tracked filings fell from 144 to 143: accession 0000019617-23-000432, the 10-Q filed 2023-08-03, was omitted.

    This confirms acceptance of a duplicated/wrong partition, not an observed live EDGAR misdelivery.

    Fix: validate observable partition identity, including the advertised start against the returned filing dates, and detect repeated history partitions. Retry rather than certify completion on disagreement. Do not simply require exact filingTo equality: real stored JPM page 001 advertises 2025-11-05 but contains filings dated 2025-11-06.

MINOR — Makefile:14,51-52
F4 is only partially closed: ordinary interpreter selection works, but the guard does not cover every requested path.

    Confirmed:
    - The python3-only PATH selects python3.
    - Python 3.9 prints the intended version message.
    - A nonexistent PYTHON exits 127 with the shell’s missing-file error.
    - PYTHON containing a space is split into separate command words because both invocations are unquoted.
    - The guard’s f-string is rejected by Python’s 3.5 grammar. An interpreter that old, including Python 2, cannot reach the intended version diagnostic.

    Fix: check interpreter availability, quote its pathname in both invocations, and use a version probe whose syntax older interpreters can parse.

MINOR — docs/morning-check.md:17
F3’s implementation/test closure is present, but the morning-check explanation remains overbroad.

    The row still says a recorded gap stays until “make backfill”. The restored never-read-with-gap case and the in-memory ingestion probe show a successful ordinary daily bootstrap clearing that gap. D8 now qualifies its statement; this operational reference does not.

    Fix: state the bootstrap exception here: already-read filers require backfill; never-read filers automatically retry their complete history.

Accepted limitations

    F5 is closed within its stated scope: the CLI is the installation target, a missing-target dry-run selects venv --clear and reinstall, and AGENTS.md documents deleting other broken virtual environments.

    No stored duplicate history partitions or current count-validation failures were found. Self-consistently wrong SEC metadata cannot be completely disproved without an independent expected filing inventory.

    Python 2 was unavailable; its diagnostic limitation was assessed from the guard syntax and the older-grammar rejection, not a Python 2 execution.

    No job commands, suites, installations, database writes, or file edits were performed. The clean-clone/Linux evidence was reviewed, not reproduced. Git status remained unchanged.
