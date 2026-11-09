"""Everything between EDGAR's JSON and the `filings` table.

Read in this order: the inputs (companies.csv, overrides.toml), how a ticker becomes a list of CIKs, how one filer's
pages are fetched and loaded (`ingest_filer`), and how filings get their fiscal label (`relabel_filer`).
"""

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


# ============================================================================ inputs

def load_companies(path: Path) -> list[str]:
    """Tickers from companies.csv (header `ticker`), upper-cased, in file order, without duplicates."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        names = [f.strip().lower() for f in (reader.fieldnames or [])]
        if "ticker" not in names:
            raise ValueError(f"{path}: expected a header row with a 'ticker' column")
        key = reader.fieldnames[names.index("ticker")]
        tickers: list[str] = []
        for row in reader:
            ticker = (row.get(key) or "").strip().upper()
            if ticker and ticker not in tickers:
                tickers.append(ticker)
    if not tickers:
        raise ValueError(f"{path}: no tickers found")
    return tickers


def fingerprint_inputs(companies_file: Path, overrides_file: Path) -> dict[str, dict]:
    """SHA-256 of the two input files as the run read them. Stored on the run row, so `make check` can tell whether
    the stored checks were evaluated against the files that exist now. An absent overrides file has no digest."""
    def one(path: Path) -> dict:
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        return {"path": path.name, "sha256": digest}
    return {"companies_csv": one(companies_file), "overrides_toml": one(overrides_file)}


@dataclass
class Overrides:
    """Facts EDGAR does not carry, each with its evidence, from overrides.toml (see that file's comments)."""
    extra_ciks: list[dict] = field(default_factory=list)                   # predecessor or current CIKs per ticker
    fiscal_year_ends: dict[str, list[dict]] = field(default_factory=dict)  # ticker -> entries, optionally dated
    naming_conventions: dict[str, dict] = field(default_factory=dict)      # ticker -> sec_year_offset, until
    filing_notes: dict[str, str] = field(default_factory=dict)             # accession -> note
    filing_corrections: dict[str, dict] = field(default_factory=dict)      # accession -> report_date, note, evidence
    accepted_findings: list[dict] = field(default_factory=list)            # check, key, note

    @classmethod
    def load(cls, path: Path) -> "Overrides":
        if not path.is_file():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))

        o = cls()
        for item in data.get("extra_ciks", []):
            if "ticker" not in item or "cik" not in item:
                raise ValueError(f"{path}: every [[extra_ciks]] entry needs ticker and cik")
            o.extra_ciks.append({**item, "ticker": str(item["ticker"]).upper(), "cik": int(item["cik"]),
                                 "current": bool(item.get("current", False)),
                                 "valid_from": _as_date(item.get("valid_from")), "valid_to": _as_date(item.get("valid_to"))})
        for item in data.get("fiscal_year_ends", []):
            mmdd = str(item.get("mmdd", ""))
            if not fiscal.valid_mmdd(mmdd):
                raise ValueError(f"{path}: fiscal_year_ends entry for {item.get('ticker')} needs a real mmdd like 0331")
            o.fiscal_year_ends.setdefault(str(item["ticker"]).upper(), []).append(
                {**item, "mmdd": mmdd, "valid_from": _as_date(item.get("valid_from")), "valid_to": _as_date(item.get("valid_to"))})
        for item in data.get("naming_conventions", []):
            if "ticker" not in item:
                raise ValueError(f"{path}: every [[naming_conventions]] entry needs a ticker")
            offset = int(item.get("sec_year_offset", -1))
            if offset not in (-1, 1):
                raise ValueError(f"{path}: naming_conventions sec_year_offset must be -1 or 1")
            o.naming_conventions[str(item["ticker"]).upper()] = {**item, "sec_year_offset": offset, "until": _as_date(item.get("until"))}
        for item in data.get("filing_notes", []):
            if "accession" not in item or "note" not in item:
                raise ValueError(f"{path}: every [[filing_notes]] entry needs accession and note")
            o.filing_notes[str(item["accession"])] = str(item["note"])
        for item in data.get("filing_corrections", []):
            if "accession" not in item or _as_date(item.get("report_date")) is None:
                raise ValueError(f"{path}: every [[filing_corrections]] entry needs accession and report_date")
            if not item.get("evidence"):
                raise ValueError(f"{path}: filing_corrections for {item['accession']} needs evidence (what was read, where)")
            o.filing_corrections[str(item["accession"])] = {**item, "report_date": _as_date(item["report_date"])}
        o.accepted_findings = [dict(item) for item in data.get("accepted_findings", [])]
        return o


# ============================================================================ companies and their CIKs

def sync_companies(conn: psycopg.Connection, tickers: list[str], overrides: Overrides) -> None:
    """Activate the listed tickers, deactivate the rest, and record the override CIKs with their evidence."""
    with conn.cursor() as cur:
        for ticker in tickers:
            cur.execute("INSERT INTO companies (ticker) VALUES (%s) ON CONFLICT (ticker) DO UPDATE SET active = true", (ticker,))
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
                (item["ticker"], item["cik"], item["current"], item["valid_from"], item["valid_to"], item.get("note"), item.get("evidence")),
            )
    conn.commit()


@dataclass
class Resolution:
    current: dict[str, int]                 # ticker -> CIK according to company_tickers.json today
    changed: list[tuple[str, int, int]]     # (ticker, previous CIK, new CIK)
    missing: list[str]                      # tickers absent from company_tickers.json with no current override
    resolved_by_override: dict[str, int]    # tickers absent from the map, resolved by an evidenced override


def resolve_ciks(conn: psycopg.Connection, client: EdgarClient, run_id: int, tickers: list[str]) -> Resolution:
    """Map every ticker to today's CIK through company_tickers.json. A CIK that changed is noticed, never adopted
    silently: the old CIK stays on the list (not current) until a person records it in overrides.toml."""
    data, _ = client.company_tickers()
    by_ticker: dict[str, tuple[int, str]] = {}
    for row in data.values():
        by_ticker.setdefault(str(row["ticker"]).upper(), (int(row["cik_str"]), str(row.get("title", ""))))
    res = Resolution(current={}, changed=[], missing=[], resolved_by_override={})
    with conn.cursor() as cur:
        cur.execute("SELECT ticker, cik FROM company_ciks WHERE source = 'sec_tickers' AND is_current")
        previous = {row["ticker"]: row["cik"] for row in cur.fetchall()}
        for ticker in tickers:
            entry = by_ticker.get(ticker)
            cur.execute("INSERT INTO ticker_cik_map (run_id, ticker, cik, title) VALUES (%s, %s, %s, %s)",
                        (run_id, ticker, entry[0] if entry else None, entry[1] if entry else None))
            if entry is None:
                cur.execute("SELECT cik FROM company_ciks WHERE ticker = %s AND source = 'override' AND is_current ORDER BY cik LIMIT 1", (ticker,))
                row = cur.fetchone()
                if row:
                    res.resolved_by_override[ticker] = res.current[ticker] = row["cik"]
                else:
                    res.missing.append(ticker)
                continue
            cik, title = entry
            res.current[ticker] = cik
            if ticker in previous and previous[ticker] != cik:
                res.changed.append((ticker, previous[ticker], cik))
            cur.execute(
                """
                INSERT INTO company_ciks (ticker, cik, source, is_current, note) VALUES (%s, %s, 'sec_tickers', true, %s)
                ON CONFLICT (ticker, cik) DO UPDATE SET is_current = true, last_seen = now()
                """,
                (ticker, cik, f"company_tickers.json title: {title}"),
            )
            cur.execute("UPDATE company_ciks SET is_current = false WHERE ticker = %s AND cik <> %s AND is_current", (ticker, cik))
    conn.commit()
    return res


def active_ciks(conn: psycopg.Connection) -> list[tuple[str, int]]:
    """Every (ticker, cik) to fetch: current CIKs, overrides, and previous CIKs nobody has retired yet."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cc.ticker, cc.cik FROM company_ciks cc JOIN companies c ON c.ticker = cc.ticker "
            "WHERE c.active ORDER BY cc.ticker, cc.is_current DESC, cc.cik"
        )
        return [(row["ticker"], row["cik"]) for row in cur.fetchall()]


# ============================================================================ one filer: fetch and load

PAGE_COLUMNS = (
    "accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "act", "fileNumber", "filmNumber",
    "primaryDocument", "primaryDocDescription", "isXBRL", "isInlineXBRL", "size", "form",
)


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


def validate_submissions(cik: int, data: dict) -> dict:
    """The page must be for this CIK with a non-empty, consistent recent block; anything else is a fetch failure."""
    got = str(data.get("cik", "")).strip().lstrip("0") or "0"
    if got != str(cik):
        raise EdgarError(f"submissions page for CIK {cik} says cik={data.get('cik')!r}")
    recent = (data.get("filings") or {}).get("recent") or {}
    if not recent.get("accessionNumber"):
        raise EdgarError(f"submissions page for CIK {cik} has an empty recent block")
    _validate_columns(f"submissions page for CIK {cik}", recent)
    return recent


def validate_history_page(cik: int, advertised: dict, page: dict) -> None:
    """A history page must hold exactly the filings the recent page advertised for it. Accepting an empty or truncated
    one would mark the filer's history as read for good, with reports missing and no check able to tell."""
    where = f"history page {advertised.get('name')} of CIK {cik}"
    count = len(page.get("accessionNumber") or [])
    if count != advertised.get("filingCount"):
        raise EdgarError(f"{where} holds {count} filings, the recent page advertised {advertised.get('filingCount')}")
    _validate_columns(where, page)


def _validate_columns(where: str, block: dict) -> None:
    """Every column as long as the accession numbers, and the two the job cannot do without present."""
    accessions = block.get("accessionNumber") or []
    for key in PAGE_COLUMNS:
        column = block.get(key)
        if column is not None and len(column) != len(accessions):
            raise EdgarError(f"{where}: column {key} has {len(column)} entries, accessionNumber has {len(accessions)}")
    for key in ("form", "filingDate"):
        if not block.get(key):
            raise EdgarError(f"{where}: column {key} is missing")


def parse_page(cik: int, page: dict) -> list[dict]:
    """Rows for the forms the job tracks, from one submissions page (the recent block or an older page)."""
    col = {key: page.get(key) or [] for key in PAGE_COLUMNS}

    def cell(key: str, i: int):
        return col[key][i] if i < len(col[key]) else None

    rows = []
    for i, form in enumerate(col["form"]):
        fam = fiscal.family(form)
        if fam is None:
            continue
        accession, document = col["accessionNumber"][i], cell("primaryDocument", i) or None
        rows.append({
            "accession_number": accession, "cik": cik, "form": form.strip(), "family": fam,
            "is_amendment": fiscal.is_amendment(form), "is_transition": fiscal.is_transition(form),
            "filing_date": _as_date(cell("filingDate", i)), "report_date": _as_date(cell("reportDate", i)),
            "acceptance_datetime": _as_datetime(cell("acceptanceDateTime", i)),
            "act": cell("act", i) or None, "file_number": cell("fileNumber", i) or None, "film_number": cell("filmNumber", i) or None,
            "primary_document": document, "primary_doc_description": cell("primaryDocDescription", i) or None,
            "primary_document_url": document_url(cik, accession, document),
            "is_xbrl": _as_bool(cell("isXBRL", i)), "is_inline_xbrl": _as_bool(cell("isInlineXBRL", i)),
            "size_bytes": cell("size", i) or None,
        })
    return rows


def ingest_filer(
    conn: psycopg.Connection, client: EdgarClient, run_id: int, cik: int, backfill: bool, overrides_fye: str | None,
    today: date, full: bool = False, seen_this_run: set[str] | None = None,
) -> IngestResult:
    """Fetch one filer's submissions page (and its history pages when due), load the filings, flag the ones that
    vanished, and advance the filer's coverage watermark. `seen_this_run` collects the accession numbers every page
    of this run listed, so a filing co-filed by two registrants is never flagged missing while either lists it."""
    data, raw = client.submissions(cik)
    recent = validate_submissions(cik, data)
    filer = _upsert_filer(conn, cik, data, overrides_fye, run_id)
    snapshot_changed = _store_snapshot(conn, cik, "recent", run_id, raw, data)

    rows = parse_page(cik, recent)
    recent_dates = [_as_date(d) for d in recent.get("filingDate") or [] if d]
    recent_window_start = min(recent_dates) if recent_dates else None
    # The disappearance window starts where this page starts, or where the previous fetch of it started if that was
    # earlier, so a filing that vanished while being the oldest on the page is still noticed.
    window_start = recent_window_start
    previous_start = filer.get("recent_window_start")
    if window_start and previous_start and previous_start < window_start:
        window_start = previous_start

    # A filer's history is read the first time it is seen, on any run, so a first run or a new company can never end
    # green with only the recent page loaded. After that only `backfill` reads it: for a gap on record, or everyone on --full.
    read_history = not filer.get("backfill_completed_at") or (backfill and (full or filer.get("coverage_gap_since")))
    pages = 1
    if read_history:
        for page in data.get("filings", {}).get("files", []) or []:
            page_data, page_raw = client.submissions_page(page["name"])
            validate_history_page(cik, page, page_data)
            _store_snapshot(conn, cik, page["name"], run_id, page_raw, page_data)
            rows.extend(parse_page(cik, page_data))
            page_from = _as_date(page.get("filingFrom"))
            if page_from and (window_start is None or page_from < window_start):
                window_start = page_from   # a history page extends the window back to its oldest filing
            pages += 1

    inserted, updated = upsert_filings(conn, rows, run_id)
    listed = {r["accession_number"] for r in rows}
    if seen_this_run is not None:
        seen_this_run |= listed
        listed = set(seen_this_run)
    newly_missing = _mark_missing(conn, cik, window_start, listed, today)
    _advance_coverage(conn, cik, recent_window_start, bool(read_history), today)
    conn.commit()
    return IngestResult(cik, data.get("name") or "", pages, len(rows), inserted, updated, newly_missing, snapshot_changed,
                        recent_window_start, bool(read_history))


def _upsert_filer(conn: psycopg.Connection, cik: int, data: dict, overrides_fye: str | None, run_id: int) -> dict:
    """Store what EDGAR says about the filer; returns the bookkeeping columns as they were before this run."""
    with conn.cursor() as cur:
        cur.execute("SELECT backfill_completed_at, coverage_gap_since, recent_window_start FROM filers WHERE cik = %s", (cik,))
        before = cur.fetchone() or {}
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
            (cik, data.get("name"), data.get("entityType"), data.get("category"), data.get("sic"), data.get("sicDescription"),
             data.get("fiscalYearEnd") or None, overrides_fye, data.get("stateOfIncorporation"),
             Jsonb(data.get("tickers") or []), Jsonb(data.get("exchanges") or []), Jsonb(data.get("formerNames") or []), run_id),
        )
    return dict(before)


# ---- the filings table -------------------------------------------------------------------------------------------

# Insert, or refresh the row when the same registrant lists it again. A later page never erases a value with NULL,
# and the document URL moves only together with a named document.
_UPSERT_OWN = """
INSERT INTO filings (accession_number, cik, form, family, is_amendment, is_transition, filing_date, report_date,
    acceptance_datetime, act, file_number, film_number, primary_document, primary_doc_description,
    primary_document_url, is_xbrl, is_inline_xbrl, size_bytes, first_seen_run, last_seen_run)
VALUES (%(accession_number)s, %(cik)s, %(form)s, %(family)s, %(is_amendment)s, %(is_transition)s, %(filing_date)s,
    %(report_date)s, %(acceptance_datetime)s, %(act)s, %(file_number)s, %(film_number)s, %(primary_document)s,
    %(primary_doc_description)s, %(primary_document_url)s, %(is_xbrl)s, %(is_inline_xbrl)s, %(size_bytes)s,
    %(run_id)s, %(run_id)s)
ON CONFLICT (accession_number) DO UPDATE SET
    form = EXCLUDED.form, family = EXCLUDED.family, is_amendment = EXCLUDED.is_amendment,
    is_transition = EXCLUDED.is_transition, filing_date = EXCLUDED.filing_date,
    report_date = COALESCE(EXCLUDED.report_date, filings.report_date),
    acceptance_datetime = COALESCE(EXCLUDED.acceptance_datetime, filings.acceptance_datetime),
    act = COALESCE(EXCLUDED.act, filings.act),
    file_number = COALESCE(EXCLUDED.file_number, filings.file_number),
    film_number = COALESCE(EXCLUDED.film_number, filings.film_number),
    primary_document = COALESCE(EXCLUDED.primary_document, filings.primary_document),
    primary_doc_description = COALESCE(EXCLUDED.primary_doc_description, filings.primary_doc_description),
    primary_document_url = CASE WHEN EXCLUDED.primary_document IS NULL THEN filings.primary_document_url
                                ELSE EXCLUDED.primary_document_url END,
    is_xbrl = COALESCE(EXCLUDED.is_xbrl, filings.is_xbrl),
    is_inline_xbrl = COALESCE(EXCLUDED.is_inline_xbrl, filings.is_inline_xbrl),
    size_bytes = COALESCE(EXCLUDED.size_bytes, filings.size_bytes),
    last_seen_run = EXCLUDED.last_seen_run, not_seen_since = NULL, updated_at = now()
WHERE filings.cik = EXCLUDED.cik
RETURNING (xmax = 0) AS inserted
"""

# The accession already belongs to another registrant: only mark it seen and remember this co-registrant.
_SEEN_BY_CO_REGISTRANT = """
UPDATE filings SET last_seen_run = %(run_id)s, not_seen_since = NULL, updated_at = now(),
    also_filed_by_ciks = CASE WHEN %(cik)s = ANY(also_filed_by_ciks) THEN also_filed_by_ciks
                              ELSE array_append(also_filed_by_ciks, %(cik)s::bigint) END
WHERE accession_number = %(accession_number)s AND cik <> %(cik)s
"""


def upsert_filings(conn: psycopg.Connection, rows: list[dict], run_id: int) -> tuple[int, int]:
    """Returns (inserted, updated). The first registrant that listed a filing owns its metadata."""
    inserted = updated = 0
    with conn.cursor() as cur:
        for row in rows:
            params = {**row, "run_id": run_id}
            cur.execute(_UPSERT_OWN, params)
            hit = cur.fetchone()
            if hit is None:
                cur.execute(_SEEN_BY_CO_REGISTRANT, params)
                updated += 1
            elif hit["inserted"]:
                inserted += 1
            else:
                updated += 1
    return inserted, updated


def _mark_missing(conn: psycopg.Connection, cik: int, window_start: date | None, seen: set[str], today: date) -> list[str]:
    """Filings this registrant listed before (as owner or co-registrant), inside the window this fetch covered, that
    no page of this run lists. Rows already flagged stay flagged until a page lists them again."""
    if window_start is None:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT accession_number FROM filings
            WHERE (cik = %s OR %s = ANY(also_filed_by_ciks)) AND filing_date >= %s AND not_seen_since IS NULL
              AND NOT (accession_number = ANY(%s))
            """,
            (cik, cik, window_start, list(seen)),
        )
        missing = [row["accession_number"] for row in cur.fetchall()]
        if missing:
            cur.execute("UPDATE filings SET not_seen_since = %s WHERE accession_number = ANY(%s)", (today, missing))
    return missing


def _store_snapshot(conn: psycopg.Connection, cik: int, page: str, run_id: int, raw: bytes, body: dict) -> bool:
    """Keep the full parsed page when its content changed; otherwise record that this run saw the same content.
    Returns True when the content changed."""
    digest = hashlib.sha256(raw).hexdigest()
    with conn.cursor() as cur:
        cur.execute("SELECT id, sha256 FROM source_snapshots WHERE cik = %s AND page = %s ORDER BY fetched_at DESC LIMIT 1", (cik, page))
        last = cur.fetchone()
        if last and last["sha256"] == digest:
            # same content: record that this run saw it too
            cur.execute("UPDATE source_snapshots SET last_seen_run = %s WHERE id = %s", (run_id, last["id"]))
            return False
        cur.execute(
            "INSERT INTO source_snapshots (cik, page, sha256, run_id, last_seen_run, body) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (cik, page, sha256) DO UPDATE SET last_seen_run = EXCLUDED.last_seen_run, fetched_at = now()",
            (cik, page, digest, run_id, run_id, Jsonb(body)),
        )
    return True


def _advance_coverage(conn: psycopg.Connection, cik: int, recent_window_start: date | None, history_read: bool, today: date) -> None:
    """The filer's coverage watermark (`coverage_through`: filings known complete through that date).

      history read                       -> coverage through today, any gap cleared, backfill marked done
      recent page overlaps the watermark -> coverage through today
      recent page starts after it        -> a gap is recorded (first date kept); the watermark does not move

    Only a history read ever clears a gap, so a run that merely reached its checks cannot bridge one. A filer's first
    run always reads its history, so every other case has a watermark to compare with."""
    with conn.cursor() as cur:
        cur.execute("SELECT coverage_through FROM filers WHERE cik = %s", (cik,))
        through = cur.fetchone()["coverage_through"]
        if history_read:
            cur.execute("UPDATE filers SET recent_window_start = %s, coverage_through = %s, coverage_gap_since = NULL, "
                        "backfill_completed_at = now() WHERE cik = %s", (recent_window_start, today, cik))
        elif recent_window_start is None or recent_window_start <= through:
            cur.execute("UPDATE filers SET recent_window_start = %s, coverage_through = %s WHERE cik = %s", (recent_window_start, today, cik))
        else:
            cur.execute("UPDATE filers SET recent_window_start = %s, coverage_gap_since = COALESCE(coverage_gap_since, %s) WHERE cik = %s",
                        (recent_window_start, today, cik))


# ============================================================================ labels, corrections, notes

def effective_fye(fiscal_year_end: str | None, entries: list[dict] | None, report_date: date | None = None) -> str | None:
    """The fiscal year end (MMDD) that applies to a period: a dated override whose validity contains the period end,
    else an undated override, else what EDGAR says, else None."""
    entries = entries or []
    dated = [e for e in entries if e.get("valid_from") or e.get("valid_to")]
    undated = [e for e in entries if e not in dated]
    if report_date is not None:
        for e in dated:
            if (e.get("valid_from") is None or e["valid_from"] <= report_date) and (e.get("valid_to") is None or report_date <= e["valid_to"]):
                return e["mmdd"]
    if undated:
        return undated[-1]["mmdd"]
    return fiscal_year_end if fiscal.valid_mmdd(fiscal_year_end) else None


def relabel_filer(conn: psycopg.Connection, cik: int, fye_entries: list[dict] | None = None) -> int:
    """Recompute every filing's label for one CIK with the current rule and overrides; returns rows changed.
    A corrected period of report takes precedence over EDGAR's and marks the label 'corrected'."""
    with conn.cursor() as cur:
        cur.execute("SELECT fiscal_year_end FROM filers WHERE cik = %s", (cik,))
        edgar_fye = (cur.fetchone() or {}).get("fiscal_year_end")
        cur.execute(
            "SELECT accession_number, form, report_date, report_date_corrected, correction_note, filing_date, "
            "fiscal_year, fiscal_period, label_method, label_note FROM filings WHERE cik = %s",
            (cik,),
        )
        changed = 0
        for row in cur.fetchall():
            period = row["report_date_corrected"] or row["report_date"]
            lab = fiscal.label(row["form"], period, effective_fye(edgar_fye, fye_entries, period), row["filing_date"])
            if row["report_date_corrected"]:
                new = (lab.fiscal_year, lab.fiscal_period, "corrected",
                       f"period of report corrected from {row['report_date']} to {row['report_date_corrected']}; {row['correction_note'] or ''}")
            else:
                new = (lab.fiscal_year, lab.fiscal_period, lab.method, lab.note or None)
            if new != (row["fiscal_year"], row["fiscal_period"], row["label_method"], row["label_note"]):
                cur.execute("UPDATE filings SET fiscal_year = %s, fiscal_period = %s, label_method = %s, label_note = %s, "
                            "updated_at = now() WHERE accession_number = %s", (*new, row["accession_number"]))
                changed += 1
    conn.commit()
    return changed


def apply_filing_corrections(conn: psycopg.Connection, corrections: dict[str, dict]) -> int:
    """Set the corrected period of report per accession, and clear corrections no longer listed."""
    changed = 0
    with conn.cursor() as cur:
        cur.execute("UPDATE filings SET report_date_corrected = NULL, correction_note = NULL "
                    "WHERE report_date_corrected IS NOT NULL AND NOT (accession_number = ANY(%s))", (list(corrections),))
        changed += cur.rowcount
        for accession, c in corrections.items():
            note = f"{c.get('note', '')} [evidence: {c.get('evidence', '')}]".strip()
            cur.execute("UPDATE filings SET report_date_corrected = %s, correction_note = %s WHERE accession_number = %s "
                        "AND (report_date_corrected IS DISTINCT FROM %s OR correction_note IS DISTINCT FROM %s)",
                        (c["report_date"], note, accession, c["report_date"], note))
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


# ============================================================================ the SEC-label refresh queue

def ciks_awaiting_sec_labels(conn: psycopg.Connection, ciks: list[int], accepted_accessions: set[str] | None = None, limit: int = 30) -> list[int]:
    """Filers with XBRL-era reports that still have no SEC label (accepted no-label cases excluded), oldest attempt
    first and never-attempted first, at most `limit` per run so nobody starves behind the cap."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT fl.cik, count(*) AS n, min(f.sec_labels_attempted_at) AS attempted
            FROM filings fl LEFT JOIN sec_labels s USING (accession_number) JOIN filers f ON f.cik = fl.cik
            WHERE fl.cik = ANY(%s) AND fl.family IN ('annual', 'quarterly') AND coalesce(fl.is_xbrl, false)
              AND s.accession_number IS NULL AND NOT (fl.accession_number = ANY(%s))
            GROUP BY fl.cik ORDER BY attempted NULLS FIRST, n DESC, fl.cik LIMIT %s
            """,
            (ciks, list(accepted_accessions or ()), limit),
        )
        return [r["cik"] for r in cur.fetchall()]


def mark_sec_labels_attempted(conn: psycopg.Connection, ciks: list[int]) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE filers SET sec_labels_attempted_at = now() WHERE cik = ANY(%s)", (ciks,))
    conn.commit()


# ============================================================================ small conversions

def _as_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _as_datetime(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_bool(value) -> bool | None:
    if value in (None, ""):
        return None
    return bool(int(value)) if str(value).isdigit() else bool(value)
