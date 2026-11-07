"""Companies, CIK resolution, fetching submissions pages, upserting filings and labelling them."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from sec_filings import fiscal
from sec_filings.edgar import EdgarClient, document_url

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------- inputs

def load_companies(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "ticker" not in [f.strip().lower() for f in reader.fieldnames]:
            raise ValueError(f"{path}: expected a header row with a 'ticker' column")
        key = next(f for f in reader.fieldnames if f.strip().lower() == "ticker")
        tickers: list[str] = []
        for row in reader:
            ticker = (row.get(key) or "").strip().upper()
            if ticker and ticker not in tickers:
                tickers.append(ticker)
    if not tickers:
        raise ValueError(f"{path}: no tickers found")
    return tickers


@dataclass
class Overrides:
    extra_ciks: list[dict] = field(default_factory=list)
    fiscal_year_ends: dict[str, dict] = field(default_factory=dict)
    accepted_findings: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Overrides":
        if not path.is_file():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        extra = []
        for item in data.get("extra_ciks", []):
            if "ticker" not in item or "cik" not in item:
                raise ValueError(f"{path}: every [[extra_ciks]] entry needs ticker and cik")
            extra.append({**item, "ticker": str(item["ticker"]).upper(), "cik": int(item["cik"])})
        fyes = {}
        for item in data.get("fiscal_year_ends", []):
            mmdd = str(item.get("mmdd", ""))
            if not fiscal.valid_mmdd(mmdd):
                raise ValueError(f"{path}: fiscal_year_ends entry for {item.get('ticker')} needs mmdd like 0331")
            fyes[str(item["ticker"]).upper()] = {**item, "mmdd": mmdd}
        accepted = [dict(item) for item in data.get("accepted_findings", [])]
        return cls(extra_ciks=extra, fiscal_year_ends=fyes, accepted_findings=accepted)


# ----------------------------------------------------------------------------- companies and CIKs

def sync_companies(conn: psycopg.Connection, tickers: list[str], overrides: Overrides) -> None:
    with conn.cursor() as cur:
        for ticker in tickers:
            cur.execute(
                "INSERT INTO companies (ticker) VALUES (%s) ON CONFLICT (ticker) DO UPDATE SET active = true",
                (ticker,),
            )
        cur.execute("UPDATE companies SET active = false WHERE ticker <> ALL(%s) AND active", (tickers,))
        for item in overrides.extra_ciks:
            if item["ticker"] not in tickers:
                log.warning("overrides.toml names %s which is not in the companies file; ignored", item["ticker"])
                continue
            cur.execute(
                """
                INSERT INTO company_ciks (ticker, cik, source, is_current, valid_from, valid_to, note, evidence)
                VALUES (%s, %s, 'override', false, %s, %s, %s, %s)
                ON CONFLICT (ticker, cik) DO UPDATE
                   SET source = 'override', valid_from = EXCLUDED.valid_from, valid_to = EXCLUDED.valid_to,
                       note = EXCLUDED.note, evidence = EXCLUDED.evidence, last_seen = now()
                """,
                (
                    item["ticker"], item["cik"],
                    _as_date(item.get("valid_from")), _as_date(item.get("valid_to")),
                    item.get("note"), item.get("evidence"),
                ),
            )
    conn.commit()


@dataclass
class Resolution:
    current: dict[str, int]                      # ticker -> CIK according to company_tickers.json today
    titles: dict[str, str]
    changed: list[tuple[str, int, int]]          # (ticker, previous CIK, new CIK)
    missing: list[str]                           # tickers absent from company_tickers.json


def resolve_ciks(conn: psycopg.Connection, client: EdgarClient, run_id: int, tickers: list[str]) -> Resolution:
    data, _ = client.company_tickers()
    by_ticker: dict[str, tuple[int, str]] = {}
    for row in data.values():
        by_ticker.setdefault(str(row["ticker"]).upper(), (int(row["cik_str"]), str(row.get("title", ""))))
    with conn.cursor() as cur:
        cur.execute("SELECT ticker, cik FROM company_ciks WHERE source = 'sec_tickers' AND is_current")
        previous = {row["ticker"]: row["cik"] for row in cur.fetchall()}
        current: dict[str, int] = {}
        titles: dict[str, str] = {}
        changed: list[tuple[str, int, int]] = []
        missing: list[str] = []
        for ticker in tickers:
            entry = by_ticker.get(ticker)
            cur.execute(
                "INSERT INTO ticker_cik_map (run_id, ticker, cik, title) VALUES (%s, %s, %s, %s)",
                (run_id, ticker, entry[0] if entry else None, entry[1] if entry else None),
            )
            if entry is None:
                missing.append(ticker)
                continue
            cik, title = entry
            current[ticker] = cik
            titles[ticker] = title
            if ticker in previous and previous[ticker] != cik:
                changed.append((ticker, previous[ticker], cik))
            cur.execute(
                """
                INSERT INTO company_ciks (ticker, cik, source, is_current, note)
                VALUES (%s, %s, 'sec_tickers', true, %s)
                ON CONFLICT (ticker, cik) DO UPDATE SET is_current = true, last_seen = now()
                """,
                (ticker, cik, f"company_tickers.json title: {title}"),
            )
            cur.execute(
                "UPDATE company_ciks SET is_current = false WHERE ticker = %s AND cik <> %s AND is_current",
                (ticker, cik),
            )
    conn.commit()
    return Resolution(current=current, titles=titles, changed=changed, missing=missing)


def active_ciks(conn: psycopg.Connection) -> list[tuple[str, int]]:
    """Every (ticker, cik) pair to fetch: current CIKs, overrides and previous CIKs that nobody has retired."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cc.ticker, cc.cik FROM company_ciks cc JOIN companies c ON c.ticker = cc.ticker
            WHERE c.active ORDER BY cc.ticker, cc.is_current DESC, cc.cik
            """
        )
        return [(row["ticker"], row["cik"]) for row in cur.fetchall()]


# ----------------------------------------------------------------------------- filings

@dataclass
class IngestResult:
    cik: int
    name: str
    pages: int
    rows: int
    inserted: int
    updated: int
    newly_missing: list[str]
    snapshot_changed: bool


def parse_page(cik: int, page: dict) -> list[dict]:
    """Rows for the forms the job tracks, from one submissions page (the 'recent' block or an older page)."""
    forms = page.get("form") or []
    columns = {key: page.get(key) or [] for key in (
        "accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "act", "fileNumber", "filmNumber",
        "primaryDocument", "primaryDocDescription", "isXBRL", "isInlineXBRL", "size",
    )}
    rows: list[dict] = []
    for i, form in enumerate(forms):
        fam = fiscal.family(form)
        if fam is None:
            continue
        accession = columns["accessionNumber"][i]
        primary_document = _get(columns["primaryDocument"], i) or None
        rows.append({
            "accession_number": accession,
            "cik": cik,
            "form": form.strip(),
            "family": fam,
            "is_amendment": fiscal.is_amendment(form),
            "is_transition": fiscal.is_transition(form),
            "filing_date": _as_date(_get(columns["filingDate"], i)),
            "report_date": _as_date(_get(columns["reportDate"], i)),
            "acceptance_datetime": _as_datetime(_get(columns["acceptanceDateTime"], i)),
            "act": _get(columns["act"], i) or None,
            "file_number": _get(columns["fileNumber"], i) or None,
            "film_number": _get(columns["filmNumber"], i) or None,
            "primary_document": primary_document,
            "primary_doc_description": _get(columns["primaryDocDescription"], i) or None,
            "primary_document_url": document_url(cik, accession, primary_document),
            "is_xbrl": _as_bool(_get(columns["isXBRL"], i)),
            "is_inline_xbrl": _as_bool(_get(columns["isInlineXBRL"], i)),
            "size_bytes": _get(columns["size"], i) or None,
        })
    return rows


def ingest_filer(
    conn: psycopg.Connection, client: EdgarClient, run_id: int, cik: int, backfill: bool, overrides_fye: str | None,
    today: date,
) -> IngestResult:
    data, raw = client.submissions(cik)
    recent = data.get("filings", {}).get("recent", {})
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO filers (cik, name, entity_type, category, sic, sic_description, fiscal_year_end,
                                fiscal_year_end_override, state_of_incorporation, tickers, exchanges, former_names,
                                fetched_at, last_run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s)
            ON CONFLICT (cik) DO UPDATE SET
                name = EXCLUDED.name, entity_type = EXCLUDED.entity_type, category = EXCLUDED.category,
                sic = EXCLUDED.sic, sic_description = EXCLUDED.sic_description,
                fiscal_year_end = EXCLUDED.fiscal_year_end, fiscal_year_end_override = EXCLUDED.fiscal_year_end_override,
                state_of_incorporation = EXCLUDED.state_of_incorporation, tickers = EXCLUDED.tickers,
                exchanges = EXCLUDED.exchanges, former_names = EXCLUDED.former_names,
                fetched_at = now(), last_run_id = EXCLUDED.last_run_id
            """,
            (
                cik, data.get("name"), data.get("entityType"), data.get("category"), data.get("sic"),
                data.get("sicDescription"), data.get("fiscalYearEnd") or None, overrides_fye,
                data.get("stateOfIncorporation"), Jsonb(data.get("tickers") or []), Jsonb(data.get("exchanges") or []),
                Jsonb(data.get("formerNames") or []), run_id,
            ),
        )
    snapshot_changed = _store_snapshot(conn, cik, "recent", run_id, raw, {"filings": {"recent": recent}, "name": data.get("name"), "fiscalYearEnd": data.get("fiscalYearEnd"), "category": data.get("category")})
    rows = parse_page(cik, recent)
    pages = 1
    if backfill:
        for page in data.get("filings", {}).get("files", []) or []:
            page_data, page_raw = client.submissions_page(page["name"])
            _store_snapshot(conn, cik, page["name"], run_id, page_raw, page_data)
            rows.extend(parse_page(cik, page_data))
            pages += 1
    inserted, updated = upsert_filings(conn, rows, run_id)
    newly_missing = _mark_missing(conn, cik, recent, {r["accession_number"] for r in rows}, today)
    conn.commit()
    return IngestResult(cik, data.get("name") or "", pages, len(rows), inserted, updated, newly_missing, snapshot_changed)


def upsert_filings(conn: psycopg.Connection, rows: list[dict], run_id: int) -> tuple[int, int]:
    inserted = updated = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                INSERT INTO filings (accession_number, cik, form, family, is_amendment, is_transition, filing_date,
                    report_date, acceptance_datetime, act, file_number, film_number, primary_document,
                    primary_doc_description, primary_document_url, is_xbrl, is_inline_xbrl, size_bytes,
                    first_seen_run, last_seen_run)
                VALUES (%(accession_number)s, %(cik)s, %(form)s, %(family)s, %(is_amendment)s, %(is_transition)s,
                    %(filing_date)s, %(report_date)s, %(acceptance_datetime)s, %(act)s, %(file_number)s,
                    %(film_number)s, %(primary_document)s, %(primary_doc_description)s, %(primary_document_url)s,
                    %(is_xbrl)s, %(is_inline_xbrl)s, %(size_bytes)s, %(run_id)s, %(run_id)s)
                ON CONFLICT (accession_number) DO UPDATE SET
                    form = EXCLUDED.form, family = EXCLUDED.family, is_amendment = EXCLUDED.is_amendment,
                    is_transition = EXCLUDED.is_transition, filing_date = EXCLUDED.filing_date,
                    report_date = EXCLUDED.report_date, acceptance_datetime = EXCLUDED.acceptance_datetime,
                    act = EXCLUDED.act, file_number = EXCLUDED.file_number, film_number = EXCLUDED.film_number,
                    primary_document = EXCLUDED.primary_document,
                    primary_doc_description = EXCLUDED.primary_doc_description,
                    primary_document_url = EXCLUDED.primary_document_url, is_xbrl = EXCLUDED.is_xbrl,
                    is_inline_xbrl = EXCLUDED.is_inline_xbrl, size_bytes = EXCLUDED.size_bytes,
                    last_seen_run = EXCLUDED.last_seen_run, not_seen_since = NULL, updated_at = now(),
                    also_filed_by_ciks = CASE
                        WHEN filings.cik <> EXCLUDED.cik AND NOT (EXCLUDED.cik = ANY(filings.also_filed_by_ciks))
                        THEN array_append(filings.also_filed_by_ciks, EXCLUDED.cik)
                        ELSE filings.also_filed_by_ciks END
                RETURNING (xmax = 0) AS inserted
                """,
                {**row, "run_id": run_id},
            )
            if cur.fetchone()["inserted"]:
                inserted += 1
            else:
                updated += 1
    return inserted, updated


def _mark_missing(conn: psycopg.Connection, cik: int, recent: dict, seen: set[str], today: date) -> list[str]:
    """Filings inside the window the recent page covers that the page no longer lists."""
    dates = [d for d in (recent.get("filingDate") or []) if d]
    if not dates:
        return []
    window_start = _as_date(min(dates))
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT accession_number FROM filings
            WHERE cik = %s AND filing_date >= %s AND not_seen_since IS NULL AND NOT (accession_number = ANY(%s))
            """,
            (cik, window_start, list(seen)),
        )
        missing = [row["accession_number"] for row in cur.fetchall()]
        if missing:
            cur.execute("UPDATE filings SET not_seen_since = %s WHERE accession_number = ANY(%s)", (today, missing))
    return missing


def _store_snapshot(conn: psycopg.Connection, cik: int, page: str, run_id: int, raw: bytes, body: dict) -> bool:
    digest = hashlib.sha256(raw).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sha256 FROM source_snapshots WHERE cik = %s AND page = %s ORDER BY fetched_at DESC LIMIT 1",
            (cik, page),
        )
        last = cur.fetchone()
        if last and last["sha256"] == digest:
            return False
        cur.execute(
            """
            INSERT INTO source_snapshots (cik, page, sha256, run_id, body) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (cik, page, sha256) DO NOTHING
            """,
            (cik, page, digest, run_id, Jsonb(body)),
        )
    return True


# ----------------------------------------------------------------------------- labels

def effective_fye(fiscal_year_end: str | None, override: str | None) -> str | None:
    if fiscal.valid_mmdd(override):
        return override
    if fiscal.valid_mmdd(fiscal_year_end):
        return fiscal_year_end
    return None


def relabel_filer(conn: psycopg.Connection, cik: int) -> int:
    """Recompute the label of every tracked filing of a CIK with its current fiscal year end; returns rows changed."""
    with conn.cursor() as cur:
        cur.execute("SELECT fiscal_year_end, fiscal_year_end_override FROM filers WHERE cik = %s", (cik,))
        filer = cur.fetchone()
        fye = effective_fye(filer["fiscal_year_end"], filer["fiscal_year_end_override"]) if filer else None
        cur.execute(
            "SELECT accession_number, form, report_date, filing_date, fiscal_year, fiscal_period, label_method, label_note FROM filings WHERE cik = %s",
            (cik,),
        )
        changed = 0
        for row in cur.fetchall():
            lab = fiscal.label(row["form"], row["report_date"], fye, row["filing_date"])
            new = (lab.fiscal_year, lab.fiscal_period, lab.method, lab.note or None)
            old = (row["fiscal_year"], row["fiscal_period"], row["label_method"], row["label_note"])
            if new != old:
                cur.execute(
                    "UPDATE filings SET fiscal_year = %s, fiscal_period = %s, label_method = %s, label_note = %s, updated_at = now() WHERE accession_number = %s",
                    (*new, row["accession_number"]),
                )
                changed += 1
    conn.commit()
    return changed


# ----------------------------------------------------------------------------- helpers

def _get(column: list, i: int):
    return column[i] if i < len(column) else None


def _as_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _as_datetime(value) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_bool(value) -> bool | None:
    if value in (None, ""):
        return None
    return bool(int(value)) if str(value).isdigit() else bool(value)
