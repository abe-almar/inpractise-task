"""Companies, CIK resolution, fetching submissions pages, upserting filings and labelling them."""

from __future__ import annotations

import csv
import hashlib
import logging
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from sec_filings import fiscal
from sec_filings.edgar import EdgarClient, EdgarError, document_url

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


def _as_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass
class Overrides:
    """Facts EDGAR does not carry, each with its evidence, from overrides.toml. See that file for the sections."""
    extra_ciks: list[dict] = field(default_factory=list)
    fiscal_year_ends: dict[str, list[dict]] = field(default_factory=dict)   # ticker -> entries with optional validity
    naming_conventions: dict[str, dict] = field(default_factory=dict)       # ticker -> sec_year_offset, until
    filing_notes: dict[str, str] = field(default_factory=dict)              # accession -> note
    filing_corrections: dict[str, dict] = field(default_factory=dict)       # accession -> report_date, note, evidence
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
            extra.append({**item, "ticker": str(item["ticker"]).upper(), "cik": int(item["cik"]),
                          "current": bool(item.get("current", False)),
                          "valid_from": _as_date(item.get("valid_from")), "valid_to": _as_date(item.get("valid_to"))})
        fyes: dict[str, list[dict]] = {}
        for item in data.get("fiscal_year_ends", []):
            mmdd = str(item.get("mmdd", ""))
            if not fiscal.valid_mmdd(mmdd):
                raise ValueError(f"{path}: fiscal_year_ends entry for {item.get('ticker')} needs a real mmdd like 0331")
            fyes.setdefault(str(item["ticker"]).upper(), []).append({
                **item, "mmdd": mmdd, "valid_from": _as_date(item.get("valid_from")), "valid_to": _as_date(item.get("valid_to")),
            })
        conventions: dict[str, dict] = {}
        for item in data.get("naming_conventions", []):
            if "ticker" not in item:
                raise ValueError(f"{path}: every [[naming_conventions]] entry needs a ticker")
            offset = int(item.get("sec_year_offset", -1))
            if offset not in (-1, 1):
                raise ValueError(f"{path}: naming_conventions sec_year_offset must be -1 or 1")
            conventions[str(item["ticker"]).upper()] = {**item, "sec_year_offset": offset, "until": _as_date(item.get("until"))}
        notes = {}
        for item in data.get("filing_notes", []):
            if "accession" not in item or "note" not in item:
                raise ValueError(f"{path}: every [[filing_notes]] entry needs accession and note")
            notes[str(item["accession"])] = str(item["note"])
        corrections: dict[str, dict] = {}
        for item in data.get("filing_corrections", []):
            if "accession" not in item or _as_date(item.get("report_date")) is None:
                raise ValueError(f"{path}: every [[filing_corrections]] entry needs accession and report_date")
            if not item.get("evidence"):
                raise ValueError(f"{path}: filing_corrections for {item['accession']} needs evidence (what was read, where)")
            corrections[str(item["accession"])] = {**item, "report_date": _as_date(item["report_date"])}
        accepted = [dict(item) for item in data.get("accepted_findings", [])]
        return cls(extra_ciks=extra, fiscal_year_ends=fyes, naming_conventions=conventions, filing_notes=notes,
                   filing_corrections=corrections, accepted_findings=accepted)


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
                VALUES (%s, %s, 'override', %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, cik) DO UPDATE
                   SET source = 'override', is_current = company_ciks.is_current OR EXCLUDED.is_current,
                       valid_from = EXCLUDED.valid_from, valid_to = EXCLUDED.valid_to,
                       note = EXCLUDED.note, evidence = EXCLUDED.evidence, last_seen = now()
                """,
                (item["ticker"], item["cik"], item["current"], item["valid_from"], item["valid_to"],
                 item.get("note"), item.get("evidence")),
            )
    conn.commit()


@dataclass
class Resolution:
    current: dict[str, int]                      # ticker -> CIK according to company_tickers.json today
    titles: dict[str, str]
    changed: list[tuple[str, int, int]]          # (ticker, previous CIK, new CIK)
    missing: list[str]                           # tickers absent from company_tickers.json with no override
    resolved_by_override: dict[str, int]         # tickers absent from the map, resolved by an evidenced override


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
        by_override: dict[str, int] = {}
        for ticker in tickers:
            entry = by_ticker.get(ticker)
            cur.execute(
                "INSERT INTO ticker_cik_map (run_id, ticker, cik, title) VALUES (%s, %s, %s, %s)",
                (run_id, ticker, entry[0] if entry else None, entry[1] if entry else None),
            )
            if entry is None:
                cur.execute(
                    "SELECT cik FROM company_ciks WHERE ticker = %s AND source = 'override' AND is_current ORDER BY cik LIMIT 1",
                    (ticker,),
                )
                row = cur.fetchone()
                if row:
                    by_override[ticker] = row["cik"]
                    current[ticker] = row["cik"]
                else:
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
    return Resolution(current=current, titles=titles, changed=changed, missing=missing, resolved_by_override=by_override)


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
    recent_window_start: date | None
    history_fetched: bool


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


def validate_submissions(cik: int, data: dict) -> dict:
    """The page must be for this CIK and carry a non-empty recent block; anything else is a fetch failure."""
    got = str(data.get("cik", "")).strip().lstrip("0") or "0"
    if got != str(cik):
        raise EdgarError(f"submissions page for CIK {cik} says cik={data.get('cik')!r}")
    recent = (data.get("filings") or {}).get("recent") or {}
    accessions = recent.get("accessionNumber") or []
    if not accessions:
        raise EdgarError(f"submissions page for CIK {cik} has an empty recent block")
    for key in ("form", "filingDate"):
        if len(recent.get(key) or []) != len(accessions):
            raise EdgarError(f"submissions page for CIK {cik}: column {key} has a different length than accessionNumber")
    return recent


def ingest_filer(
    conn: psycopg.Connection, client: EdgarClient, run_id: int, cik: int, backfill: bool, overrides_fye: str | None,
    today: date, full: bool = False,
) -> IngestResult:
    data, raw = client.submissions(cik)
    recent = validate_submissions(cik, data)
    with conn.cursor() as cur:
        cur.execute("SELECT backfill_completed_at FROM filers WHERE cik = %s", (cik,))
        row = cur.fetchone()
        history_done = bool(row and row["backfill_completed_at"])
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
    snapshot_changed = _store_snapshot(conn, cik, "recent", run_id, raw, data)
    rows = parse_page(cik, recent)
    recent_dates = [_as_date(d) for d in recent.get("filingDate") or [] if d]
    window_start = min(recent_dates) if recent_dates else None
    recent_window_start = window_start
    pages = 1
    history_fetched = False
    if backfill and (full or not history_done):
        for page in data.get("filings", {}).get("files", []) or []:
            page_data, page_raw = client.submissions_page(page["name"])
            _store_snapshot(conn, cik, page["name"], run_id, page_raw, page_data)
            rows.extend(parse_page(cik, page_data))
            page_from = _as_date(page.get("filingFrom"))
            if page_from and (window_start is None or page_from < window_start):
                window_start = page_from
            pages += 1
        history_fetched = True
    inserted, updated = upsert_filings(conn, rows, run_id)
    newly_missing = _mark_missing(conn, cik, window_start, {r["accession_number"] for r in rows}, today)
    with conn.cursor() as cur:
        cur.execute("UPDATE filers SET recent_window_start = %s WHERE cik = %s", (recent_window_start, cik))
        if history_fetched:
            cur.execute("UPDATE filers SET backfill_completed_at = now() WHERE cik = %s", (cik,))
    conn.commit()
    return IngestResult(cik, data.get("name") or "", pages, len(rows), inserted, updated, newly_missing, snapshot_changed,
                        recent_window_start, history_fetched)


UPSERT_SQL = """
INSERT INTO filings (accession_number, cik, form, family, is_amendment, is_transition, filing_date,
    report_date, acceptance_datetime, act, file_number, film_number, primary_document,
    primary_doc_description, primary_document_url, is_xbrl, is_inline_xbrl, size_bytes,
    first_seen_run, last_seen_run)
VALUES (%(accession_number)s, %(cik)s, %(form)s, %(family)s, %(is_amendment)s, %(is_transition)s,
    %(filing_date)s, %(report_date)s, %(acceptance_datetime)s, %(act)s, %(file_number)s,
    %(film_number)s, %(primary_document)s, %(primary_doc_description)s, %(primary_document_url)s,
    %(is_xbrl)s, %(is_inline_xbrl)s, %(size_bytes)s, %(run_id)s, %(run_id)s)
ON CONFLICT (accession_number) DO UPDATE SET
    -- the registrant that first listed the filing owns its metadata; another registrant's page only marks it seen
    form = CASE WHEN filings.cik = EXCLUDED.cik THEN EXCLUDED.form ELSE filings.form END,
    family = CASE WHEN filings.cik = EXCLUDED.cik THEN EXCLUDED.family ELSE filings.family END,
    is_amendment = CASE WHEN filings.cik = EXCLUDED.cik THEN EXCLUDED.is_amendment ELSE filings.is_amendment END,
    is_transition = CASE WHEN filings.cik = EXCLUDED.cik THEN EXCLUDED.is_transition ELSE filings.is_transition END,
    filing_date = CASE WHEN filings.cik = EXCLUDED.cik THEN EXCLUDED.filing_date ELSE filings.filing_date END,
    -- nullable fields: a later page never erases a value with nothing
    report_date = CASE WHEN filings.cik = EXCLUDED.cik THEN COALESCE(EXCLUDED.report_date, filings.report_date) ELSE filings.report_date END,
    acceptance_datetime = CASE WHEN filings.cik = EXCLUDED.cik THEN COALESCE(EXCLUDED.acceptance_datetime, filings.acceptance_datetime) ELSE filings.acceptance_datetime END,
    act = CASE WHEN filings.cik = EXCLUDED.cik THEN COALESCE(EXCLUDED.act, filings.act) ELSE filings.act END,
    file_number = CASE WHEN filings.cik = EXCLUDED.cik THEN COALESCE(EXCLUDED.file_number, filings.file_number) ELSE filings.file_number END,
    film_number = CASE WHEN filings.cik = EXCLUDED.cik THEN COALESCE(EXCLUDED.film_number, filings.film_number) ELSE filings.film_number END,
    primary_document = CASE WHEN filings.cik = EXCLUDED.cik THEN COALESCE(EXCLUDED.primary_document, filings.primary_document) ELSE filings.primary_document END,
    primary_doc_description = CASE WHEN filings.cik = EXCLUDED.cik THEN COALESCE(EXCLUDED.primary_doc_description, filings.primary_doc_description) ELSE filings.primary_doc_description END,
    primary_document_url = CASE WHEN filings.cik = EXCLUDED.cik THEN COALESCE(EXCLUDED.primary_document_url, filings.primary_document_url) ELSE filings.primary_document_url END,
    is_xbrl = CASE WHEN filings.cik = EXCLUDED.cik THEN COALESCE(EXCLUDED.is_xbrl, filings.is_xbrl) ELSE filings.is_xbrl END,
    is_inline_xbrl = CASE WHEN filings.cik = EXCLUDED.cik THEN COALESCE(EXCLUDED.is_inline_xbrl, filings.is_inline_xbrl) ELSE filings.is_inline_xbrl END,
    size_bytes = CASE WHEN filings.cik = EXCLUDED.cik THEN COALESCE(EXCLUDED.size_bytes, filings.size_bytes) ELSE filings.size_bytes END,
    last_seen_run = EXCLUDED.last_seen_run, not_seen_since = NULL, updated_at = now(),
    also_filed_by_ciks = CASE
        WHEN filings.cik <> EXCLUDED.cik AND NOT (EXCLUDED.cik = ANY(filings.also_filed_by_ciks))
        THEN array_append(filings.also_filed_by_ciks, EXCLUDED.cik)
        ELSE filings.also_filed_by_ciks END
RETURNING (xmax = 0) AS inserted
"""


def upsert_filings(conn: psycopg.Connection, rows: list[dict], run_id: int) -> tuple[int, int]:
    inserted = updated = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(UPSERT_SQL, {**row, "run_id": run_id})
            if cur.fetchone()["inserted"]:
                inserted += 1
            else:
                updated += 1
    return inserted, updated


def _mark_missing(conn: psycopg.Connection, cik: int, window_start: date | None, seen: set[str], today: date) -> list[str]:
    """Filings inside the window this fetch covered (the recent page, or all history on a backfill) that the
    pages no longer list. Rows already flagged stay flagged until they reappear."""
    if window_start is None:
        return []
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
    """Keep the full parsed page when its content changed; otherwise record that this run saw the same content."""
    digest = hashlib.sha256(raw).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, sha256 FROM source_snapshots WHERE cik = %s AND page = %s ORDER BY fetched_at DESC LIMIT 1",
            (cik, page),
        )
        last = cur.fetchone()
        if last and last["sha256"] == digest:
            cur.execute("UPDATE source_snapshots SET last_seen_run = %s WHERE id = %s", (run_id, last["id"]))
            return False
        cur.execute(
            """
            INSERT INTO source_snapshots (cik, page, sha256, run_id, last_seen_run, body) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (cik, page, sha256) DO UPDATE SET last_seen_run = EXCLUDED.last_seen_run, fetched_at = now()
            """,
            (cik, page, digest, run_id, run_id, Jsonb(body)),
        )
    return True


def apply_filing_corrections(conn: psycopg.Connection, corrections: dict[str, dict]) -> int:
    """Set (or clear) the corrected period of report per accession. EDGAR's own value stays in report_date."""
    changed = 0
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE filings SET report_date_corrected = NULL, correction_note = NULL WHERE report_date_corrected IS NOT NULL AND NOT (accession_number = ANY(%s))",
            (list(corrections.keys()),),
        )
        changed += cur.rowcount
        for accession, c in corrections.items():
            note = f"{c.get('note', '')} [evidence: {c.get('evidence', '')}]".strip()
            cur.execute(
                "UPDATE filings SET report_date_corrected = %s, correction_note = %s WHERE accession_number = %s AND (report_date_corrected IS DISTINCT FROM %s OR correction_note IS DISTINCT FROM %s)",
                (c["report_date"], note, accession, c["report_date"], note),
            )
            changed += cur.rowcount
    conn.commit()
    return changed


def apply_filing_notes(conn: psycopg.Connection, notes: dict[str, str]) -> int:
    changed = 0
    with conn.cursor() as cur:
        for accession, note in notes.items():
            cur.execute("UPDATE filings SET note = %s WHERE accession_number = %s AND note IS DISTINCT FROM %s", (note, accession, note))
            changed += cur.rowcount
    conn.commit()
    return changed


# ----------------------------------------------------------------------------- labels

def effective_fye(fiscal_year_end: str | None, entries: list[dict] | None, report_date: date | None = None) -> str | None:
    """The fiscal year end that applies to a period: a dated override whose validity contains the period end, else
    an undated override, else what EDGAR says."""
    entries = entries or []
    if report_date is not None:
        for e in entries:
            if e.get("valid_from") or e.get("valid_to"):
                if (e.get("valid_from") is None or e["valid_from"] <= report_date) and (e.get("valid_to") is None or report_date <= e["valid_to"]):
                    return e["mmdd"]
    undated = [e for e in entries if not (e.get("valid_from") or e.get("valid_to"))]
    if undated:
        return undated[-1]["mmdd"]
    return fiscal_year_end if fiscal.valid_mmdd(fiscal_year_end) else None


def relabel_filer(conn: psycopg.Connection, cik: int, fye_entries: list[dict] | None = None) -> int:
    """Recompute the label of every tracked filing of a CIK with the fiscal year end that applies to each period;
    returns rows changed."""
    with conn.cursor() as cur:
        cur.execute("SELECT fiscal_year_end FROM filers WHERE cik = %s", (cik,))
        filer = cur.fetchone()
        edgar_fye = filer["fiscal_year_end"] if filer else None
        cur.execute(
            "SELECT accession_number, form, report_date, report_date_corrected, correction_note, filing_date, fiscal_year, fiscal_period, label_method, label_note FROM filings WHERE cik = %s",
            (cik,),
        )
        changed = 0
        for row in cur.fetchall():
            period = row["report_date_corrected"] or row["report_date"]
            fye = effective_fye(edgar_fye, fye_entries, period)
            lab = fiscal.label(row["form"], period, fye, row["filing_date"])
            if row["report_date_corrected"]:
                note = f"period of report corrected from {row['report_date']} to {row['report_date_corrected']}; {row['correction_note'] or ''}"
                new = (lab.fiscal_year, lab.fiscal_period, "corrected", note)
            else:
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
