"""The job as one plain function a scheduler can call. No Prefect import; see README for the Prefect wrapper."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from sec_filings import checks as checks_mod
from sec_filings import db, loader
from sec_filings.edgar import EdgarClient
from sec_filings.settings import Settings

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    run_id: int
    mode: str
    status: str                                   # 'ok' | 'failed'
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
    checks: list[checks_mod.CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok" and not checks_mod.hard_failures(self.checks)


def daily_run(settings: Settings, mode: str = "daily", client: EdgarClient | None = None, today: date | None = None) -> RunResult:
    """Fetch, load, label and check. `mode` is 'daily' (recent pages only) or 'backfill' (every page)."""
    if mode not in ("daily", "backfill"):
        raise ValueError("mode must be 'daily' or 'backfill'")
    today = today or datetime.now(timezone.utc).date()
    started = time.monotonic()
    client = client or EdgarClient(settings.user_agent, settings.rate_limit_per_sec, settings.http_timeout, settings.max_retries)
    tickers = loader.load_companies(settings.companies_file)
    overrides = loader.Overrides.load(settings.overrides_file)

    conn = db.connect(settings.database_url)
    try:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO runs (mode) VALUES (%s) RETURNING id", (mode,))
            run_id = cur.fetchone()["id"]
        conn.commit()
        result = RunResult(run_id=run_id, mode=mode, status="running")
        try:
            loader.sync_companies(conn, tickers, overrides)
            resolution = loader.resolve_ciks(conn, client, run_id, tickers)
            for ticker, old, new in resolution.changed:
                log.warning("%s moved from CIK %s to CIK %s in company_tickers.json", ticker, old, new)
            pairs = loader.active_ciks(conn)
            result.ciks_expected = len(pairs)
            newly_missing: dict[int, list[str]] = {}
            for ticker, cik in pairs:
                fye_override = (overrides.fiscal_year_ends.get(ticker) or {}).get("mmdd")
                ingest = loader.ingest_filer(conn, client, run_id, cik, mode == "backfill", fye_override, today)
                result.ciks_fetched += 1
                result.filings_seen += ingest.rows
                result.filings_inserted += ingest.inserted
                result.filings_updated += ingest.updated
                if ingest.newly_missing:
                    newly_missing[cik] = ingest.newly_missing
                result.relabelled += loader.relabel_filer(conn, cik)
                log.info("%s CIK %s %s: %d pages, %d tracked filings, %d new, %d seen again%s",
                         ticker, cik, ingest.name, ingest.pages, ingest.rows, ingest.inserted, ingest.updated,
                         "" if not ingest.newly_missing else f", {len(ingest.newly_missing)} VANISHED")
            result.status = "ok"
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            result.status = "failed"
            result.error = f"{type(exc).__name__}: {exc}"
            log.exception("run %s failed", run_id)
            conn.rollback()
        result.requests = client.stats.requests
        result.http_errors = client.stats.http_errors
        result.duration_sec = time.monotonic() - started
        if result.status == "ok":
            facts = checks_mod.RunFacts(
                run_id=run_id, ciks_expected=result.ciks_expected, ciks_fetched=result.ciks_fetched,
                http_errors=client.stats.http_errors, retries=client.stats.retries, duration_sec=result.duration_sec,
                time_budget_sec=settings.run_time_budget_sec, missing_tickers=resolution.missing,
                changed_tickers=resolution.changed, newly_missing=newly_missing,
            )
            result.checks = checks_mod.run_checks(conn, facts, overrides.accepted_findings, today)
        _finish_run(conn, result)
        return result
    finally:
        conn.close()


def _finish_run(conn: psycopg.Connection, r: RunResult) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE runs SET finished_at = now(), status = %s, checks_passed = %s, requests = %s, http_errors = %s,
                   ciks_expected = %s, ciks_fetched = %s, filings_seen = %s, filings_inserted = %s,
                   filings_updated = %s, error = %s, notes = %s
            WHERE id = %s
            """,
            (
                r.status, (not checks_mod.hard_failures(r.checks)) if r.status == "ok" else None, r.requests,
                r.http_errors, r.ciks_expected, r.ciks_fetched, r.filings_seen, r.filings_inserted,
                r.filings_updated, r.error, Jsonb({"relabelled": r.relabelled, "duration_sec": round(r.duration_sec, 1)}),
                r.run_id,
            ),
        )
    conn.commit()
