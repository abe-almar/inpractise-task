"""The job as one plain function a scheduler can call: `daily_run`. No Prefect import; see README for the wrapper.

A run is: open a run row, load every filer (companies -> CIKs -> pages -> filings), apply the human overrides and
relabel, refresh the SEC's own labels where needed, run the morning checks, close the run row.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from sec_filings import checks as checks_mod
from sec_filings import db, loader, verify
from sec_filings.edgar import EdgarClient, EdgarError
from sec_filings.settings import Settings

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    run_id: int
    mode: str
    status: str                                   # 'running' | 'ok' | 'failed'
    ciks_expected: int = 0
    ciks_fetched: int = 0
    filings_seen: int = 0
    filings_inserted: int = 0
    filings_updated: int = 0
    relabelled: int = 0
    requests: int = 0
    http_errors: int = 0
    duration_sec: float = 0.0
    error: str | None = None
    fetch_errors: dict[int, str] = field(default_factory=dict)
    checks: list[checks_mod.CheckResult] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)      # loader.fingerprint_inputs: the files this run read

    @property
    def ok(self) -> bool:
        return self.status == "ok" and not checks_mod.hard_failures(self.checks)


@dataclass
class _Loaded:
    """What the load step hands to the checks."""
    resolution: loader.Resolution
    window_starts: dict[int, date | None] = field(default_factory=dict)     # per fetched CIK
    newly_missing: dict[int, list[str]] = field(default_factory=dict)
    ciks_with_new_reports: list[int] = field(default_factory=list)
    sec_labels_refreshed: list[int] = field(default_factory=list)
    sec_label_refresh_errors: dict[int, str] = field(default_factory=dict)


def daily_run(settings: Settings, mode: str = "daily", client: EdgarClient | None = None, today: date | None = None,
              full: bool = False) -> RunResult:
    """Fetch, load, label and check. A filer's history pages are read the first time it is seen, in either mode. After
    that `mode` 'daily' reads recent pages only and 'backfill' also re-reads history: for a filer with a coverage gap, for
    everyone with `full=True`."""
    if mode not in ("daily", "backfill"):
        raise ValueError("mode must be 'daily' or 'backfill'")
    today = today or datetime.now(timezone.utc).date()
    started = time.monotonic()
    client = client or EdgarClient(settings.user_agent, settings.rate_limit_per_sec, settings.http_timeout, settings.max_retries)
    tickers = loader.load_companies(settings.companies_file)
    overrides = loader.Overrides.load(settings.overrides_file)
    inputs = loader.fingerprint_inputs(settings.companies_file, settings.overrides_file)

    conn = db.connect(settings.database_url)
    try:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO runs (mode) VALUES (%s) RETURNING id", (mode,))
            run_id = cur.fetchone()["id"]
        conn.commit()
        result = RunResult(run_id=run_id, mode=mode, status="running", inputs=inputs)
        loaded = None
        try:
            loaded = _load_everything(conn, client, run_id, tickers, overrides, mode == "backfill", full, today, result)
            result.status = "ok"
        except Exception as exc:  # noqa: BLE001 - recorded on the run row, reported by the CLI
            result.status = "failed"
            result.error = f"{type(exc).__name__}: {exc}"
            log.exception("run %s failed", run_id)
            conn.rollback()
        if loaded is not None and result.status == "ok":
            _refresh_sec_labels(conn, client, overrides, loaded)
        result.requests, result.http_errors = client.stats.requests, client.stats.http_errors
        result.duration_sec = time.monotonic() - started
        if loaded is not None and result.status == "ok":
            facts = checks_mod.RunFacts(
                run_id=run_id, ciks_expected=result.ciks_expected, ciks_fetched=result.ciks_fetched,
                http_errors=client.stats.http_errors, retries=client.stats.retries, duration_sec=result.duration_sec,
                time_budget_sec=settings.run_time_budget_sec, missing_tickers=loaded.resolution.missing,
                resolved_by_override=loaded.resolution.resolved_by_override, changed_tickers=loaded.resolution.changed,
                newly_missing=loaded.newly_missing, fetch_errors=result.fetch_errors,
                sec_labels_refreshed=loaded.sec_labels_refreshed, sec_label_refresh_errors=loaded.sec_label_refresh_errors,
            )
            result.checks = checks_mod.run_checks(conn, facts, overrides, today)
        _close_run(conn, result)
        return result
    finally:
        conn.close()


def _load_everything(conn: psycopg.Connection, client: EdgarClient, run_id: int, tickers: list[str], overrides: loader.Overrides,
                     backfill: bool, full: bool, today: date, result: RunResult) -> _Loaded:
    """Companies -> CIKs -> one fetch per CIK -> overrides -> labels. A filer whose page fails is recorded in
    result.fetch_errors and skipped; the run goes on and `run_complete` fails."""
    loader.sync_companies(conn, tickers, overrides)
    loaded = _Loaded(resolution=loader.resolve_ciks(conn, client, run_id, tickers))
    for ticker, old, new in loaded.resolution.changed:
        log.warning("%s moved from CIK %s to CIK %s in company_tickers.json", ticker, old, new)
    pairs = loader.active_ciks(conn)
    result.ciks_expected = len(pairs)
    seen_this_run: set[str] = set()
    for ticker, cik in pairs:
        fye_entries = overrides.fiscal_year_ends.get(ticker) or []
        # stored so the fiscal_year_end_known check sees that a year end is known even when only dated entries exist
        fye_override = loader.effective_fye(None, fye_entries, today) or (fye_entries[-1]["mmdd"] if fye_entries else None)
        try:
            ingest = loader.ingest_filer(conn, client, run_id, cik, backfill, fye_override, today, full=full, seen_this_run=seen_this_run)
        except EdgarError as exc:
            conn.rollback()
            result.fetch_errors[cik] = str(exc)
            log.error("%s CIK %s: %s", ticker, cik, exc)
            continue
        result.ciks_fetched += 1
        result.filings_seen += ingest.rows
        result.filings_inserted += ingest.inserted
        result.filings_updated += ingest.updated
        loaded.window_starts[cik] = ingest.recent_window_start
        if ingest.newly_missing:
            loaded.newly_missing[cik] = ingest.newly_missing
        if ingest.inserted:
            loaded.ciks_with_new_reports.append(cik)
        log.info("%s CIK %s %s: %d pages, %d tracked filings, %d new, %d seen again%s", ticker, cik, ingest.name, ingest.pages,
                 ingest.rows, ingest.inserted, ingest.updated, "" if not ingest.newly_missing else f", {len(ingest.newly_missing)} VANISHED")
    loader.apply_filing_corrections(conn, overrides.filing_corrections)
    loader.apply_filing_notes(conn, overrides.filing_notes)
    for ticker, cik in pairs:
        if cik in loaded.window_starts:   # fetched this run
            result.relabelled += loader.relabel_filer(conn, cik, overrides.fiscal_year_ends.get(ticker) or [])
    return loaded


def _refresh_sec_labels(conn: psycopg.Connection, client: EdgarClient, overrides: loader.Overrides, loaded: _Loaded) -> None:
    """Keep the SEC cross-check current: filers with new reports, plus filers whose XBRL reports still lack a SEC
    label (retried every run, oldest attempt first). A failure is recorded and fails `sec_labels_refreshed`."""
    accepted_no_label = {str(a.get("key")) for a in overrides.accepted_findings if a.get("check") == "labels_match_sec"}
    queue = set(loaded.ciks_with_new_reports) | set(loader.ciks_awaiting_sec_labels(conn, list(loaded.window_starts), accepted_no_label))
    for cik in sorted(queue):
        loader.mark_sec_labels_attempted(conn, [cik])
        try:
            verify.refresh_sec_labels(conn, client, [cik])
            loaded.sec_labels_refreshed.append(cik)
        except EdgarError as exc:
            conn.rollback()
            loaded.sec_label_refresh_errors[cik] = str(exc)


def _close_run(conn: psycopg.Connection, r: RunResult) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE runs SET finished_at = now(), status = %s, checks_passed = %s, requests = %s, http_errors = %s,
                   ciks_expected = %s, ciks_fetched = %s, filings_seen = %s, filings_inserted = %s,
                   filings_updated = %s, error = %s, notes = %s
            WHERE id = %s
            """,
            (r.status, (not checks_mod.hard_failures(r.checks)) if r.status == "ok" else None, r.requests, r.http_errors,
             r.ciks_expected, r.ciks_fetched, r.filings_seen, r.filings_inserted, r.filings_updated, r.error,
             Jsonb({"relabelled": r.relabelled, "duration_sec": round(r.duration_sec, 1),
                    "fetch_errors": {str(k): v for k, v in r.fetch_errors.items()}, "inputs": r.inputs}),
             r.run_id),
        )
    conn.commit()
