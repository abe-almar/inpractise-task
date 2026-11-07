"""Command line: `sec-filings run` is the one command the scheduler calls."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from sec_filings import checks as checks_mod
from sec_filings import db, report
from sec_filings.edgar import EdgarClient
from sec_filings.flow import daily_run
from sec_filings.settings import Settings, SettingsError, load_dotenv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sec-filings", description="Keep a Postgres table of the annual and quarterly reports companies filed with the SEC.")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="daily run: fetch the recent filings of every company, load, label, check (exit 1 if a hard check fails)")
    b = sub.add_parser("backfill", help="like run, but also reads the older submissions pages of every filer whose history has not been read yet (first run, or after adding a company)")
    b.add_argument("--full", action="store_true", help="re-read every older page of every filer, even those already read")
    sub.add_parser("check", help="re-read the latest run's stored checks without fetching anything; exit 1 if it is not a clean run from today")
    sub.add_parser("verify-labels", help="load the SEC's own fiscal labels from the XBRL companyfacts API for every filer, then evaluate the labels_match_sec check (exit 1 on a real disagreement)")
    r = sub.add_parser("report", help="what do we have for a company's report, e.g. report AAPL 2009 FY")
    r.add_argument("ticker")
    r.add_argument("fiscal_year", type=int)
    r.add_argument("period", nargs="?", default="FY", help="FY (default), Q1, Q2 or Q3")
    sub.add_parser("summary", help="one line per company: CIKs, counts, first and last period")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    load_dotenv(Path(".env"))
    try:
        settings = Settings.from_env()
    except SettingsError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if args.command in ("run", "backfill"):
        result = daily_run(settings, mode="daily" if args.command == "run" else "backfill", full=getattr(args, "full", False))
        print(f"run {result.run_id} ({result.mode}): status={result.status} filers={result.ciks_fetched}/{result.ciks_expected} "
              f"filings seen={result.filings_seen} new={result.filings_inserted} relabelled={result.relabelled} "
              f"requests={result.requests} errors={result.http_errors} duration={result.duration_sec:.0f}s")
        if result.error:
            print(f"error: {result.error}")
        if result.checks:
            print(checks_mod.format_results(result.checks))
        print("RESULT: OK" if result.ok else "RESULT: ATTENTION NEEDED")
        return 0 if result.ok else 1
    conn = db.connect(settings.database_url)
    try:
        if args.command == "check":
            return cmd_check(conn)
        if args.command == "verify-labels":
            from sec_filings.loader import Overrides
            from sec_filings.verify import verify_labels
            db.migrate(conn)
            client = EdgarClient(settings.user_agent, settings.rate_limit_per_sec, settings.http_timeout, settings.max_retries)
            out = verify_labels(conn, client)
            print(json.dumps(out))
            overrides = Overrides.load(settings.overrides_file)
            check = checks_mod.apply_accepted(
                checks_mod.check_labels_match_sec(conn, overrides.naming_conventions, datetime.now(timezone.utc).date()),
                overrides.accepted_findings,
            )
            print(checks_mod.format_results([check]))
            return 0 if check.passed else 1
        if args.command == "report":
            rows = report.answer(conn, args.ticker, args.fiscal_year, args.period)
            if not rows:
                print(f"nothing on record for {args.ticker.upper()} fiscal {args.fiscal_year} {args.period.upper()}")
                return 1
            for row in rows:
                print(json.dumps(row, default=str, indent=2))
            return 0
        if args.command == "summary":
            for row in report.summary(conn):
                print(json.dumps(row, default=str))
            return 0
    finally:
        conn.close()
    return 2


def cmd_check(conn) -> int:
    run = report.latest_run(conn)
    if run is None:
        print("no run on record")
        return 1
    today = datetime.now(timezone.utc).date()
    started = run["started_at"].astimezone(timezone.utc).date()
    rows = report.checks_of_run(conn, run["id"])
    results = [checks_mod.CheckResult(r["name"], r["severity"], r["passed"], r["summary"], r["detail"]) for r in rows]
    ran_today = started == today and run["status"] == "ok"
    print(f"latest run {run['id']} ({run['mode']}) started {run['started_at']:%Y-%m-%d %H:%M %Z} status={run['status']} "
          f"filers={run['ciks_fetched']}/{run['ciks_expected']} new filings={run['filings_inserted']} errors={run['http_errors']}")
    print(f"[{'ok' if ran_today else 'FAIL'}] ran_today: " + ("a successful run started today (UTC)" if ran_today else "no successful run today; the scheduler did not run it, or it failed"))
    if run["error"]:
        print(f"error: {run['error']}")
    print(checks_mod.format_results(results))
    ok = ran_today and not checks_mod.hard_failures(results)
    print("RESULT: OK" if ok else "RESULT: ATTENTION NEEDED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
