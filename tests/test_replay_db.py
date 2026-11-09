"""Replay tests (docs/decisions.md D5 and D10) against a fresh Postgres schema with a fake EDGAR client that serves
the trimmed real pages in tests/fixtures. Runs only when TEST_DATABASE_URL is set, for example

    TEST_DATABASE_URL=postgresql://sec:sec@localhost:25432/sec_filings pytest tests/test_replay_db.py

No network: every page, and every companyfacts document, comes from the fixtures or from this file."""

from __future__ import annotations

import copy
import json
import os
import secrets
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from sec_filings import checks, db, fiscal, loader, report, verify
from sec_filings.edgar import COMPANYFACTS_URL, SUBMISSIONS_URL, TICKERS_URL, EdgarError, EdgarNotFound, Stats, document_url
from sec_filings.flow import daily_run
from sec_filings.settings import Settings

from conftest import FIXTURE_FILES, TRACKED_FORMS, all_fixtures, load_fixture

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set TEST_DATABASE_URL (e.g. postgresql://sec:sec@localhost:25432/sec_filings) to run the Postgres replay test",
)

TODAY = date(2026, 11, 7)          # the day the fixtures were fetched; fixed so the overdue check is deterministic
AAPL, XOM_NEW, XOM_OLD = 320193, 2115436, 34088
SHARED_Q2_2026_10Q = "0000034088-26-000093"   # listed on both Exxon pages (same accession, period 2026-06-30)
APPLE_1997_10QA = "0000320193-97-000021"      # EDGAR's period of report is the filing date; corrected in overrides
MIGRATIONS = ["001_schema.sql"]
CHECK_NAMES = {
    "run_complete", "http_health", "tickers_resolved", "cik_changes_acknowledged", "fiscal_year_end_known", "history_gap",
    "sec_labels_refreshed", "no_filings_lost", "labels_complete", "labels_unique", "labels_unique_across_ciks",
    "labels_inferred_for_amendments", "labels_far_from_calendar", "overdue_reports", "labels_match_sec", "news",
}
NO_GAP = "every filer's recent page overlaps its coverage watermark; no coverage gap on record"
GAP_REMEDY = "run `make backfill` (it re-reads the history of the filers with a gap)"

CORRECTION_TOML = f"""
[[filing_corrections]]
accession = "{APPLE_1997_10QA}"
report_date = 1997-03-28
note = "Apple 10-Q/A filed 1997-10-07 amends the 10-Q for the quarter ended 28 March 1997 (fiscal 1997 Q2)"
evidence = "cover page of https://www.sec.gov/Archives/edgar/data/320193/0000320193-97-000021.txt, read 2026-11-07"
"""

XOM_OVERRIDE_TOML = f"""
[[extra_ciks]]
ticker = "XOM"
cik = {XOM_OLD}
name = "Exxon Mobil Corp"
valid_to = 2026-07-01
note = "holding company reorganisation on 2026-07-01; the ticker now maps to CIK {XOM_NEW}"
evidence = "tests/fixtures/CIK0000034088.json and CIK0002115436.json"
"""


class FakeEdgarClient:
    """Serves company_tickers.json, submissions pages and companyfacts from dicts, with the real client's interface.
    The dicts are the test's to mutate between runs; every answer is a deep copy. Like the real client, a request
    that EDGAR answers with 404 still counts as a request."""

    def __init__(self, pages: dict[int, dict], older_pages: dict[str, dict] | None = None,
                 companyfacts: dict[int, dict] | None = None) -> None:
        self.pages = pages
        self.older_pages = older_pages if older_pages is not None else {}
        self.facts = companyfacts if companyfacts is not None else {}
        self.tickers = {
            str(i): {"cik_str": cik, "ticker": page["tickers"][0], "title": page["name"]}
            for i, (cik, page) in enumerate(sorted(pages.items()))
            if page.get("tickers")
        }
        self.stats = Stats()

    def _serve(self, url: str, body: dict | None) -> tuple[dict, bytes]:
        self.stats.requests += 1
        self.stats.urls.append(url)
        if body is None:
            raise EdgarNotFound(f"{url}: HTTP 404")
        raw = json.dumps(body, sort_keys=True).encode("utf-8")
        self.stats.bytes += len(raw)
        return copy.deepcopy(body), raw

    def company_tickers(self) -> tuple[dict, bytes]:
        return self._serve(TICKERS_URL, self.tickers)

    def submissions(self, cik: int) -> tuple[dict, bytes]:
        return self._serve(SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json"), self.pages.get(cik))

    def submissions_page(self, name: str) -> tuple[dict, bytes]:
        return self._serve(SUBMISSIONS_URL.format(name=name), self.older_pages.get(name))

    def companyfacts(self, cik: int) -> tuple[dict, bytes]:
        return self._serve(COMPANYFACTS_URL.format(cik=cik), self.facts.get(cik))


def raw_of(body: dict) -> bytes:
    """The bytes FakeEdgarClient serves for a body (what the loader hashes for source_snapshots)."""
    return json.dumps(body, sort_keys=True).encode("utf-8")


# ----------------------------------------------------------------------------- fixtures

@pytest.fixture
def schema():
    """A fresh schema with the job's tables, dropped afterwards whatever happens."""
    name = f"test_{secrets.token_hex(6)}"
    admin = db.connect(DATABASE_URL)
    admin.autocommit = True
    admin.execute(f"CREATE SCHEMA {name}")
    admin.execute(f"SET search_path TO {name}")
    try:
        assert db.migrate(admin) == MIGRATIONS
        assert db.migrate(admin) == []          # idempotent
        yield name, admin
    finally:
        admin.execute(f"DROP SCHEMA {name} CASCADE")
        admin.close()


def run_url(name: str) -> str:
    # daily_run opens its own connection: the schema travels in the connection string
    return DATABASE_URL + ("&" if "?" in DATABASE_URL else "?") + f"options=-c%20search_path%3D{name}"


def make_settings(name: str, tmp_path: Path, tickers: list[str], overrides_toml: str) -> Settings:
    companies = tmp_path / "companies.csv"
    companies.write_text("ticker\n" + "\n".join(tickers) + "\n", encoding="utf-8")
    overrides = tmp_path / "overrides.toml"
    overrides.write_text(overrides_toml, encoding="utf-8")
    return Settings(
        database_url=run_url(name), user_agent="sec-filings tests tests@example.com", rate_limit_per_sec=5.0,
        companies_file=companies, overrides_file=overrides,
    )


@pytest.fixture
def settings(schema, tmp_path: Path) -> Settings:
    name, _ = schema
    return make_settings(name, tmp_path, ["AAPL", "XOM"], XOM_OVERRIDE_TOML + CORRECTION_TOML)


# ----------------------------------------------------------------------------- helpers

def _tracked(page: dict) -> list[str]:
    recent = page["filings"]["recent"]
    return [a for a, f in zip(recent["accessionNumber"], recent["form"]) if f in TRACKED_FORMS]


def _xbrl_reports(page: dict) -> set[str]:
    recent = page["filings"]["recent"]
    return {a for a, f, x in zip(recent["accessionNumber"], recent["form"], recent["isXBRL"])
            if f in TRACKED_FORMS and not f.startswith("NT") and x == 1}


def _accession(page: dict, form: str, report_date: str) -> str:
    recent = page["filings"]["recent"]
    hits = [a for a, f, r in zip(recent["accessionNumber"], recent["form"], recent["reportDate"]) if f == form and r == report_date]
    assert len(hits) == 1, (form, report_date, hits)
    return hits[0]


def _min_filing_date(page: dict) -> date:
    return min(date.fromisoformat(d) for d in page["filings"]["recent"]["filingDate"] if d)


def _remove_accession(page: dict, accession: str) -> None:
    recent = page["filings"]["recent"]
    i = recent["accessionNumber"].index(accession)
    for values in recent.values():
        if isinstance(values, list):
            del values[i]


def _notes(conn, run_id: int) -> dict:
    return conn.execute("SELECT notes FROM runs WHERE id = %s", (run_id,)).fetchone()["notes"]


def _checks(conn, run_id: int) -> dict[str, dict]:
    rows = report.checks_of_run(conn, run_id)
    assert {r["name"] for r in rows} == CHECK_NAMES and len(rows) == len(CHECK_NAMES)
    return {r["name"]: r for r in rows}


def _hard_failures(result) -> list[str]:
    return [c.name for c in checks.hard_failures(result.checks)]


def _url_kinds(client: FakeEdgarClient) -> Counter:
    kinds: Counter = Counter()
    for u in client.stats.urls:
        kinds["tickers" if u == TICKERS_URL else "companyfacts" if "companyfacts" in u else "history" if "submissions-0" in u else "submissions"] += 1
    return kinds


def _history_urls(client: FakeEdgarClient) -> list[str]:
    return [u.rsplit("/", 1)[1] for u in client.stats.urls if "submissions-0" in u]


def _coverage(conn) -> dict[int, tuple[date | None, date | None]]:
    return {r["cik"]: (r["coverage_through"], r["coverage_gap_since"])
            for r in conn.execute("SELECT cik, coverage_through, coverage_gap_since FROM filers").fetchall()}


PAGE_KEYS = ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "act", "form", "fileNumber", "filmNumber",
             "items", "core_type", "size", "isXBRL", "isInlineXBRL", "isXBRLNumeric", "primaryDocument", "primaryDocDescription")


def synthetic_page(cik: int, name: str, ticker: str, fye: str, rows: list[dict]) -> dict:
    """A hand-made submissions page: rows are dicts with accession, form, filed, period and xbrl (0/1)."""
    recent: dict[str, list] = {k: [] for k in PAGE_KEYS}
    for r in rows:
        recent["accessionNumber"].append(r["accession"])
        recent["filingDate"].append(str(r["filed"]))
        recent["reportDate"].append(str(r["period"]))
        recent["acceptanceDateTime"].append(f"{r['filed']}T21:00:00.000Z")
        recent["act"].append("34")
        recent["form"].append(r["form"])
        recent["fileNumber"].append("001-00001")
        recent["filmNumber"].append("1")
        recent["items"].append("")
        recent["core_type"].append("XBRL" if r["xbrl"] else "")
        recent["size"].append(1000)
        recent["isXBRL"].append(r["xbrl"])
        recent["isInlineXBRL"].append(r["xbrl"])
        recent["isXBRLNumeric"].append(r["xbrl"])
        recent["primaryDocument"].append(f"{r['accession']}.htm")
        recent["primaryDocDescription"].append(r["form"])
    return {"cik": f"{cik:010d}", "name": name, "entityType": "operating", "category": "Large accelerated filer",
            "fiscalYearEnd": fye, "tickers": [ticker], "exchanges": ["Nasdaq"], "formerNames": [],
            "filings": {"recent": recent, "files": []}}


def _companyfacts(labels: dict[str, tuple[int | None, str | None, str]]) -> dict:
    """A minimal companyfacts document: one fact per accession carrying the SEC's fy/fp label."""
    facts = [{"accn": accn, "form": form, "fy": fy, "fp": fp, "end": "2026-01-01", "val": 1, "filed": "2026-01-02"}
             for accn, (fy, fp, form) in labels.items()]
    return {"cik": 320193, "entityName": "Apple Inc.", "facts": {"us-gaap": {"Revenues": {"units": {"USD": facts}}}}}


def agreeing_labels(page: dict) -> dict[str, tuple[int | None, str | None, str]]:
    """SEC labels that agree with our rule for every XBRL report on a page."""
    cik, fye = int(page["cik"]), page["fiscalYearEnd"]
    xbrl = _xbrl_reports(page)
    out = {}
    for r in loader.parse_page(cik, page["filings"]["recent"]):
        if r["accession_number"] in xbrl:
            lab = fiscal.label(r["form"], r["report_date"], fye, r["filing_date"])
            out[r["accession_number"]] = (lab.fiscal_year, lab.fiscal_period, r["form"])
    return out


def agreeing_facts(pages: dict[int, dict]) -> dict[int, dict]:
    return {cik: _companyfacts(agreeing_labels(page)) for cik, page in pages.items()}


def add_history(client: FakeEdgarClient, cik: int) -> str:
    """Give a filer one older page (holding a single untracked 8-K) so a history read is visible in the requests."""
    name = f"CIK{cik:010d}-submissions-001.json"
    client.pages[cik]["filings"]["files"] = [{"name": name, "filingCount": 1, "filingFrom": "2000-01-05", "filingTo": "2000-01-05"}]
    row = {"accession": f"{cik:010d}-00-000001", "form": "8-K", "filed": date(2000, 1, 5), "period": date(2000, 1, 5), "xbrl": 0}
    client.older_pages[name] = synthetic_page(cik, "history", "H", "1231", [row])["filings"]["recent"]
    return name


# ----------------------------------------------------------------------------- the replay

def test_replay_ten_runs(schema, settings, tmp_path: Path):
    name, conn = schema
    pages = all_fixtures()                       # pristine copies for the expectations
    client = FakeEdgarClient(copy.deepcopy(pages), companyfacts=agreeing_facts(pages))   # the client's own copies, mutated between runs
    rows_per_page = {cik: _tracked(pages[cik]) for cik in (AAPL, XOM_NEW, XOM_OLD)}
    total_rows = sum(len(v) for v in rows_per_page.values())
    unique_rows = len(set().union(*rows_per_page.values()))
    xbrl_reports = set().union(*(_xbrl_reports(pages[cik]) for cik in (AAPL, XOM_NEW, XOM_OLD)))
    assert total_rows == unique_rows + 1    # the one 10-Q both Exxon pages list
    assert SHARED_Q2_2026_10Q in rows_per_page[XOM_NEW] and SHARED_Q2_2026_10Q in rows_per_page[XOM_OLD]
    assert APPLE_1997_10QA in rows_per_page[AAPL]
    labels_ok = (f"0 real disagreement(s) with the SEC's XBRL label on {len(xbrl_reports)} filings compared; "
                 "0 XBRL filing(s) older than 21 days still without a SEC label; 0 recent filing(s) awaiting one")

    def run(mode: str, today: date, full: bool = False):
        before = _url_kinds(client)
        result = daily_run(settings, mode=mode, client=client, today=today, full=full)
        delta = _url_kinds(client)
        delta.subtract(before)
        return result, +delta

    # ======================================================================= run 1: backfill into an empty schema
    r1, calls = run("backfill", TODAY)
    assert r1.status == "ok", r1.error
    assert r1.mode == "backfill"
    assert r1.fetch_errors == {}
    assert (r1.ciks_expected, r1.ciks_fetched) == (3, 3)
    assert r1.filings_seen == total_rows
    assert r1.filings_inserted == unique_rows
    assert r1.filings_updated == 1
    assert r1.relabelled == unique_rows
    # no older pages (none listed); every filer gained reports, so its SEC labels are fetched, backfill or not
    assert calls == {"tickers": 1, "submissions": 3, "companyfacts": 3}
    assert r1.requests == 7 and r1.http_errors == 0
    assert r1.ok, _hard_failures(r1)
    assert conn.execute("SELECT count(*) AS n FROM filings").fetchone()["n"] == unique_rows
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE fiscal_year IS NULL").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM source_snapshots").fetchone()["n"] == 3
    assert conn.execute("SELECT count(*) AS n FROM sec_labels").fetchone()["n"] == len(xbrl_reports)
    assert _notes(conn, r1.run_id) == {"relabelled": unique_rows, "duration_sec": _notes(conn, r1.run_id)["duration_sec"], "fetch_errors": {},
                                       "inputs": loader.fingerprint_inputs(settings.companies_file, settings.overrides_file)}
    c1 = _checks(conn, r1.run_id)
    assert c1["history_gap"]["passed"] is True and c1["history_gap"]["summary"] == NO_GAP and c1["history_gap"]["detail"] == {"items": []}
    assert c1["sec_labels_refreshed"]["summary"] == "SEC labels refreshed for 3 filer(s)"
    assert c1["sec_labels_refreshed"]["detail"] == {"items": [], "refreshed": [XOM_OLD, AAPL, XOM_NEW]}
    assert c1["labels_match_sec"]["passed"] is True and c1["labels_match_sec"]["severity"] == "hard"
    assert c1["labels_match_sec"]["summary"] == labels_ok
    assert c1["labels_match_sec"]["detail"] == {"items": [], "compared": len(xbrl_reports), "naming_convention_counts": {},
                                                "pending_sec_label": 0, "older_without_sec_label": 0}
    assert c1["news"]["summary"] == f"{unique_rows} new filing(s) this run, of which 6 amendment(s) or late-filing notice(s); SEC labels refreshed for 3 filer(s)"

    # the per-filer bookkeeping: recent window start, the backfill marker, the coverage watermark, no gap
    filers = {r["cik"]: r for r in conn.execute("SELECT cik, recent_window_start, backfill_completed_at, coverage_through, coverage_gap_since, fiscal_year_end_override FROM filers").fetchall()}
    assert set(filers) == {AAPL, XOM_NEW, XOM_OLD}
    for cik in filers:
        assert filers[cik]["recent_window_start"] == _min_filing_date(pages[cik])
        assert filers[cik]["backfill_completed_at"] is not None
        assert filers[cik]["coverage_through"] == TODAY and filers[cik]["coverage_gap_since"] is None
        assert filers[cik]["fiscal_year_end_override"] is None

    # ---- the corrected period of report (overrides.toml [[filing_corrections]]) drives the label
    corrected = conn.execute(
        "SELECT report_date, report_date_corrected, correction_note, fiscal_year, fiscal_period, label_method, label_note FROM filings WHERE accession_number = %s",
        (APPLE_1997_10QA,),
    ).fetchone()
    assert corrected["report_date"] == date(1997, 10, 7)               # EDGAR's value is kept
    assert corrected["report_date_corrected"] == date(1997, 3, 28)
    assert corrected["correction_note"].startswith("Apple 10-Q/A filed 1997-10-07") and "[evidence: cover page" in corrected["correction_note"]
    assert (corrected["fiscal_year"], corrected["fiscal_period"], corrected["label_method"]) == (1997, "Q2", "corrected")
    assert corrected["label_note"].startswith("period of report corrected from 1997-10-07 to 1997-03-28; Apple 10-Q/A")
    assert conn.execute("SELECT label_method, count(*) AS n FROM filings GROUP BY 1 ORDER BY 1").fetchall() == [
        {"label_method": "corrected", "n": 1}, {"label_method": "rule", "n": unique_rows - 1},
    ]
    assert c1["labels_inferred_for_amendments"]["passed"] is True
    assert c1["labels_far_from_calendar"]["passed"] is True
    assert c1["labels_far_from_calendar"]["summary"] == "every period end sits on the company's fiscal calendar"
    q2_1997 = report.answer(conn, "AAPL", 1997, "Q2")
    assert len(q2_1997) == 1 and q2_1997[0]["accession_number"] == "0000320193-97-000011"
    assert q2_1997[0]["amendments"] == 1 and q2_1997[0]["amendment_accessions"] == [APPLE_1997_10QA]
    assert q2_1997[0]["report_date_corrected"] is None and q2_1997[0]["note"] is None
    assert report.answer(conn, "AAPL", 1997, "Q3")[0]["amendments"] == 0

    # ======================================================================= run 2: daily, nothing new
    r2, calls = run("daily", TODAY)
    assert r2.status == "ok", r2.error
    assert r2.ok, _hard_failures(r2)
    assert r2.run_id == r1.run_id + 1
    assert r2.filings_inserted == 0
    assert r2.filings_seen == r1.filings_seen == total_rows
    assert r2.filings_updated == r1.filings_inserted + r1.filings_updated == total_rows
    assert r2.relabelled == 0
    assert calls == {"tickers": 1, "submissions": 3}             # every XBRL report has its label: nothing to retry
    assert r2.requests == client.stats.requests == 11            # RunResult.requests mirrors the client's (cumulative) stats
    assert conn.execute("SELECT count(*) AS n FROM filings").fetchone()["n"] == unique_rows
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE first_seen_run = %s", (r2.run_id,)).fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE last_seen_run <> %s", (r2.run_id,)).fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE not_seen_since IS NOT NULL").fetchone()["n"] == 0
    # unchanged pages are not stored twice; the snapshot records the last run that saw the same content
    snaps = conn.execute("SELECT cik, page, run_id, last_seen_run FROM source_snapshots ORDER BY cik").fetchall()
    assert [(s["cik"], s["page"], s["run_id"], s["last_seen_run"]) for s in snaps] == [
        (XOM_OLD, "recent", r1.run_id, r2.run_id), (AAPL, "recent", r1.run_id, r2.run_id), (XOM_NEW, "recent", r1.run_id, r2.run_id),
    ]
    # the watermark advanced: the recent pages overlap it
    assert _coverage(conn) == {AAPL: (TODAY, None), XOM_NEW: (TODAY, None), XOM_OLD: (TODAY, None)}

    # ---- the checks of run 2 are on record
    c2 = _checks(conn, r2.run_id)
    hard = {n for n, c in c2.items() if c["severity"] == "hard"}
    assert hard == {"run_complete", "tickers_resolved", "cik_changes_acknowledged", "fiscal_year_end_known", "history_gap",
                    "sec_labels_refreshed", "no_filings_lost", "labels_complete", "labels_unique", "labels_inferred_for_amendments",
                    "labels_match_sec"}
    assert all(c2[n]["passed"] for n in hard)
    assert c2["cik_changes_acknowledged"]["passed"] is True
    assert c2["run_complete"]["summary"] == "fetched and validated 3 of 3 filer pages"
    assert c2["history_gap"]["summary"] == NO_GAP
    assert c2["sec_labels_refreshed"]["summary"] == "SEC labels refreshed for 0 filer(s)"
    assert c2["no_filings_lost"]["summary"] == "no filing has disappeared from the EDGAR feed"
    assert c2["labels_unique_across_ciks"]["passed"] is True and c2["labels_unique_across_ciks"]["severity"] == "soft"
    assert c2["labels_match_sec"]["summary"] == labels_ok
    assert c2["news"]["summary"] == "0 new filing(s) this run, of which 0 amendment(s) or late-filing notice(s)"
    overdue = c2["overdue_reports"]
    assert overdue["passed"] is True
    # Apple's 52/53-week calendar (year end 0926 is not a month end): the next period end is 13 weeks after the last
    # report; Exxon's calendar year gives the nominal next quarter
    assert [(e["key"], e["deadline"], e["filer_category"], e["late_notice"]) for e in overdue["detail"]["expected_next"]] == [
        ("AAPL/quarterly/2026-12-26", "2027-02-04", "large_accelerated", None),
        ("XOM/annual/2026-12-31", "2027-03-01", "large_accelerated", None),
    ]
    assert conn.execute("SELECT checks_passed, status FROM runs WHERE id = %s", (r2.run_id,)).fetchone() == {"checks_passed": True, "status": "ok"}

    # ---- the reports view: AAPL 2009 FY is one 10-K with its amendment attached, and the SEC agrees
    rows = report.answer(conn, "aapl", 2009, "fy")
    assert len(rows) == 1
    row = rows[0]
    assert row["form"] == "10-K"
    assert row["accession_number"] == "0001193125-09-214859"
    assert row["cik"] == AAPL
    assert row["report_date"] == date(2009, 9, 26)
    assert row["label_method"] == "rule"
    assert row["amendments"] == 1
    assert row["amendment_accessions"] == ["0001193125-10-012091"]
    assert row["other_originals"] is None
    assert row["also_filed_by_ciks"] == []
    assert (row["sec_fiscal_year"], row["sec_fiscal_period"]) == (2009, "FY")
    assert row["report_date_corrected"] is None and row["note"] is None and row["not_seen_since"] is None
    assert row["primary_document_url"] == "https://www.sec.gov/Archives/edgar/data/320193/000119312509214859/d10k.htm"
    assert report.answer(conn, "AAPL", 2005, "FY")[0]["sec_fiscal_year"] is None      # pre-XBRL: no label exists
    q1 = report.answer(conn, "AAPL", 2026, "Q1")
    assert [r["report_date"] for r in q1] == [date(2025, 12, 27)]
    assert report.answer(conn, "AAPL", 2026, "Q4") == []

    # ---- XOM: two CIKs, the history under the old one, the current one owns the shared filing's metadata
    ciks = {r["cik"]: r for r in conn.execute("SELECT cik, source, is_current, valid_to FROM company_ciks WHERE ticker = 'XOM'").fetchall()}
    assert set(ciks) == {XOM_NEW, XOM_OLD}
    assert ciks[XOM_NEW] == {"cik": XOM_NEW, "source": "sec_tickers", "is_current": True, "valid_to": None}
    assert ciks[XOM_OLD] == {"cik": XOM_OLD, "source": "override", "is_current": False, "valid_to": date(2026, 7, 1)}
    fy2025 = report.answer(conn, "XOM", 2025, "FY")
    assert len(fy2025) == 1 and fy2025[0]["cik"] == XOM_OLD and fy2025[0]["form"] == "10-K"
    q3 = report.answer(conn, "XOM", 2026, "Q3")
    assert len(q3) == 1 and q3[0]["cik"] == XOM_NEW and q3[0]["accession_number"] == "0002115436-26-000020"
    # The 2026 Q2 10-Q is one combined filing (accession prefix 0000034088, the old CIK's) that both pages list
    # (D2, revised). One accession is one row: it stays with the holding company, which is ingested first as the
    # current CIK; the old CIK's page only adds itself to also_filed_by_ciks and leaves the metadata alone.
    q2 = report.answer(conn, "XOM", 2026, "Q2")
    assert len(q2) == 1
    assert q2[0]["accession_number"] == SHARED_Q2_2026_10Q
    assert q2[0]["cik"] == XOM_NEW
    assert q2[0]["also_filed_by_ciks"] == [XOM_OLD]
    assert q2[0]["other_originals"] is None
    assert q2[0]["form"] == "10-Q" and q2[0]["report_date"] == date(2026, 6, 30)
    assert q2[0]["primary_document_url"] == document_url(XOM_NEW, SHARED_Q2_2026_10Q, "xom-20260630.htm")
    assert (q2[0]["sec_fiscal_year"], q2[0]["sec_fiscal_period"]) == (2026, "Q2")
    shared = conn.execute("SELECT cik, file_number, film_number, primary_document_url FROM filings WHERE accession_number = %s", (SHARED_Q2_2026_10Q,)).fetchone()
    new_page = pages[XOM_NEW]["filings"]["recent"]
    i = new_page["accessionNumber"].index(SHARED_Q2_2026_10Q)
    assert shared == {"cik": XOM_NEW, "file_number": new_page["fileNumber"][i], "film_number": new_page["filmNumber"][i],
                      "primary_document_url": document_url(XOM_NEW, SHARED_Q2_2026_10Q, new_page["primaryDocument"][i])}
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE also_filed_by_ciks <> '{}'").fetchone()["n"] == 1
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE cik = %s", (XOM_OLD,)).fetchone()["n"] == len(rows_per_page[XOM_OLD]) - 1
    # the view reaches the filing through the co-registrant as well, yet shows it once per ticker
    assert conn.execute("SELECT count(*) AS n FROM reports WHERE accession_number = %s", (SHARED_Q2_2026_10Q,)).fetchone()["n"] == 1
    summary = {r["ticker"]: r for r in report.summary(conn)}
    assert summary["XOM"]["ciks"] == f"{XOM_NEW},{XOM_OLD}"
    assert summary["XOM"]["name"] == "ExxonMobil Holdings Corp"
    assert summary["AAPL"]["annual"] == sum(1 for f in pages[AAPL]["filings"]["recent"]["form"] if f in ("10-K", "10-K405"))
    assert summary["AAPL"]["late_notices"] == 2
    assert summary["AAPL"]["odd_labels"] == 1       # the corrected 10-Q/A
    assert summary["AAPL"]["first_annual"] == date(1994, 9, 30)

    # ======================================================================= run 3: a filing vanishes from the feed
    gone = _accession(pages[AAPL], "10-Q", "2025-12-27")
    _remove_accession(client.pages[AAPL], gone)
    r3, _ = run("daily", TODAY)
    assert r3.status == "ok" and r3.filings_inserted == 0 and r3.relabelled == 0
    assert r3.filings_seen == total_rows - 1
    assert r3.ok is False and _hard_failures(r3) == ["no_filings_lost"]
    assert conn.execute("SELECT count(*) AS n FROM filings").fetchone()["n"] == unique_rows   # never deleted
    lost = conn.execute("SELECT not_seen_since, last_seen_run, first_seen_run, filing_date FROM filings WHERE accession_number = %s", (gone,)).fetchone()
    assert (lost["not_seen_since"], lost["last_seen_run"], lost["first_seen_run"]) == (TODAY, r2.run_id, r1.run_id)
    c3 = _checks(conn, r3.run_id)
    assert c3["no_filings_lost"]["passed"] is False and c3["no_filings_lost"]["severity"] == "hard"
    assert c3["no_filings_lost"]["summary"].startswith("1 filing(s) no longer listed by EDGAR (1 new this run)")
    assert c3["no_filings_lost"]["detail"]["items"] == [{
        "key": gone, "ticker": "AAPL", "cik": AAPL, "form": "10-Q", "filed": str(lost["filing_date"]),
        "not_seen_since": str(TODAY), "new_this_run": True,
    }]
    assert conn.execute("SELECT checks_passed FROM runs WHERE id = %s", (r3.run_id,)).fetchone()["checks_passed"] is False
    # the reports view still answers, and says the filing is no longer listed
    assert report.answer(conn, "AAPL", 2026, "Q1")[0]["not_seen_since"] == TODAY
    # the snapshot changed, so a new one is stored
    assert conn.execute("SELECT count(*) AS n FROM source_snapshots WHERE cik = %s", (AAPL,)).fetchone()["n"] == 2

    # ======================================================================= run 4: still missing the next day
    r4, _ = run("daily", TODAY + timedelta(days=1))
    assert r4.status == "ok" and r4.ok is False and _hard_failures(r4) == ["no_filings_lost"]
    c4 = _checks(conn, r4.run_id)
    assert c4["no_filings_lost"]["summary"].startswith("1 filing(s) no longer listed by EDGAR (0 new this run)")
    assert [(i["key"], i["new_this_run"], i["not_seen_since"]) for i in c4["no_filings_lost"]["detail"]["items"]] == [(gone, False, str(TODAY))]
    assert conn.execute("SELECT not_seen_since FROM filings WHERE accession_number = %s", (gone,)).fetchone()["not_seen_since"] == TODAY
    assert conn.execute("SELECT count(*) AS n FROM source_snapshots").fetchone()["n"] == 4
    # the watermark still advances: the pages overlap it
    assert _coverage(conn) == {AAPL: (TODAY + timedelta(days=1), None), XOM_NEW: (TODAY + timedelta(days=1), None), XOM_OLD: (TODAY + timedelta(days=1), None)}

    # ======================================================================= run 5: it is back, and the correction is withdrawn
    client.pages[AAPL] = copy.deepcopy(pages[AAPL])
    settings.overrides_file.write_text(XOM_OVERRIDE_TOML, encoding="utf-8")
    r5, _ = run("daily", TODAY + timedelta(days=2))
    assert r5.status == "ok"
    assert r5.filings_inserted == 0                  # a reappearing filing is an update, not a new row
    assert r5.filings_seen == total_rows
    assert conn.execute("SELECT not_seen_since, first_seen_run, last_seen_run FROM filings WHERE accession_number = %s", (gone,)).fetchone() == {
        "not_seen_since": None, "first_seen_run": r1.run_id, "last_seen_run": r5.run_id}
    c5 = _checks(conn, r5.run_id)
    assert c5["no_filings_lost"]["passed"] is True
    # without the correction the 10-Q/A is labelled from its filing date again, which a person must accept
    assert r5.relabelled == 1
    back = conn.execute("SELECT report_date_corrected, correction_note, fiscal_year, fiscal_period, label_method FROM filings WHERE accession_number = %s", (APPLE_1997_10QA,)).fetchone()
    assert back == {"report_date_corrected": None, "correction_note": None, "fiscal_year": 1997, "fiscal_period": "Q3", "label_method": "rule_filing_date"}
    assert _hard_failures(r5) == ["labels_inferred_for_amendments"]
    assert c5["labels_inferred_for_amendments"]["detail"]["items"] == [{
        "key": APPLE_1997_10QA, "ticker": "AAPL", "form": "10-Q/A", "filed": "1997-10-07", "label": "1997 Q3",
        "document": document_url(AAPL, APPLE_1997_10QA, None),
    }]
    assert c5["labels_far_from_calendar"]["passed"] is True     # a filing-date label is listed, not a failure
    assert [i["method"] for i in c5["labels_far_from_calendar"]["detail"]["items"]] == ["rule_filing_date"]

    # ======================================================================= run 6: correction back; the holding company's watermark is old
    settings.overrides_file.write_text(XOM_OVERRIDE_TOML + CORRECTION_TOML, encoding="utf-8")
    conn.execute("UPDATE filers SET coverage_through = %s WHERE cik = %s", (date(2026, 1, 1), XOM_NEW))
    r6, _ = run("daily", TODAY + timedelta(days=3))
    assert r6.status == "ok" and r6.relabelled == 1
    assert conn.execute("SELECT label_method, fiscal_period FROM filings WHERE accession_number = %s", (APPLE_1997_10QA,)).fetchone() == {"label_method": "corrected", "fiscal_period": "Q2"}
    assert _hard_failures(r6) == ["history_gap"]
    c6 = _checks(conn, r6.run_id)
    assert c6["history_gap"]["detail"] == {"items": [{
        "key": str(XOM_NEW), "ticker": "XOM", "cik": XOM_NEW, "gap_since": str(TODAY + timedelta(days=3)),
        "recent_page_starts": str(_min_filing_date(pages[XOM_NEW])),
    }]}
    assert c6["history_gap"]["summary"] == f"1 filer(s) with a coverage gap on record (recent page started after the filer's coverage watermark); {GAP_REMEDY}"
    assert date(2026, 1, 1) < _min_filing_date(pages[XOM_NEW])    # the page starts after the watermark: filings may have passed unseen
    # the gapped filer's watermark does not move; the others' do
    assert _coverage(conn) == {AAPL: (TODAY + timedelta(days=3), None), XOM_OLD: (TODAY + timedelta(days=3), None),
                               XOM_NEW: (date(2026, 1, 1), TODAY + timedelta(days=3))}
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE not_seen_since IS NOT NULL").fetchone()["n"] == 0

    # ======================================================================= run 7: one page fails validation
    client.pages[XOM_OLD]["cik"] = "0000999999"
    r7, calls = run("daily", TODAY + timedelta(days=4))
    assert r7.status == "ok" and r7.error is None          # the run goes on without that filer
    assert r7.fetch_errors == {XOM_OLD: f"submissions page for CIK {XOM_OLD} says cik='0000999999'"}
    assert (r7.ciks_expected, r7.ciks_fetched) == (3, 2)
    assert r7.filings_seen == total_rows - len(rows_per_page[XOM_OLD])
    assert r7.relabelled == 0
    assert calls == {"tickers": 1, "submissions": 3}
    assert _hard_failures(r7) == ["run_complete", "history_gap"]    # the gap stays on record until a history read
    c7 = _checks(conn, r7.run_id)
    assert c7["run_complete"]["summary"] == "fetched and validated 2 of 3 filer pages; 1 failed"
    assert c7["run_complete"]["detail"]["items"] == [{"key": str(XOM_OLD), "cik": XOM_OLD, "error": r7.fetch_errors[XOM_OLD]}]
    assert c7["no_filings_lost"]["passed"] is True          # a failed fetch marks nothing as missing
    assert c7["history_gap"]["detail"]["items"][0]["gap_since"] == str(TODAY + timedelta(days=3))   # the first date is kept
    assert _notes(conn, r7.run_id)["fetch_errors"] == {str(XOM_OLD): r7.fetch_errors[XOM_OLD]}
    run_row = conn.execute("SELECT status, checks_passed, ciks_fetched, ciks_expected FROM runs WHERE id = %s", (r7.run_id,)).fetchone()
    assert run_row == {"status": "ok", "checks_passed": False, "ciks_fetched": 2, "ciks_expected": 3}
    assert conn.execute("SELECT last_run_id FROM filers WHERE cik = %s", (XOM_OLD,)).fetchone()["last_run_id"] == r6.run_id
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE cik = %s AND last_seen_run = %s", (XOM_OLD, r7.run_id)).fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM filings").fetchone()["n"] == unique_rows
    assert _coverage(conn)[XOM_OLD] == (TODAY + timedelta(days=3), None)    # an unfetched filer keeps yesterday's watermark

    # ======================================================================= run 8: the co-filed 10-Q leaves the holding company's page
    client.pages[XOM_OLD]["cik"] = f"{XOM_OLD:010d}"
    _remove_accession(client.pages[XOM_NEW], SHARED_Q2_2026_10Q)
    r8, _ = run("daily", TODAY + timedelta(days=5))
    assert r8.status == "ok" and r8.fetch_errors == {}
    assert r8.filings_seen == total_rows - 1 and r8.filings_inserted == 0
    assert _hard_failures(r8) == ["history_gap"]
    c8 = _checks(conn, r8.run_id)
    assert c8["no_filings_lost"]["passed"] is True        # the old CIK's page still lists it: not a disappearance
    shared = conn.execute("SELECT cik, also_filed_by_ciks, not_seen_since, last_seen_run FROM filings WHERE accession_number = %s", (SHARED_Q2_2026_10Q,)).fetchone()
    assert shared == {"cik": XOM_NEW, "also_filed_by_ciks": [XOM_OLD], "not_seen_since": None, "last_seen_run": r8.run_id}
    assert report.answer(conn, "XOM", 2026, "Q2")[0]["not_seen_since"] is None
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE not_seen_since IS NOT NULL").fetchone()["n"] == 0

    # ======================================================================= run 9: a plain backfill re-reads the gapped filer's history
    client.pages[XOM_NEW] = copy.deepcopy(pages[XOM_NEW])
    history_names = {cik: add_history(client, cik) for cik in (AAPL, XOM_NEW, XOM_OLD)}
    r9, calls = run("backfill", TODAY + timedelta(days=6))
    assert r9.status == "ok" and r9.ok, _hard_failures(r9)
    assert calls == {"tickers": 1, "submissions": 3, "history": 1}
    assert _history_urls(client) == [history_names[XOM_NEW]]     # only the filer with a gap; the others were done once
    assert r9.filings_inserted == 0                                # the older page holds nothing the job tracks
    c9 = _checks(conn, r9.run_id)
    assert c9["history_gap"]["summary"] == NO_GAP and c9["history_gap"]["detail"] == {"items": []}
    assert _coverage(conn) == {AAPL: (TODAY + timedelta(days=6), None), XOM_OLD: (TODAY + timedelta(days=6), None),
                               XOM_NEW: (TODAY + timedelta(days=6), None)}
    assert conn.execute("SELECT count(*) AS n FROM source_snapshots WHERE cik = %s AND page <> 'recent'", (XOM_NEW,)).fetchone()["n"] == 1

    # ======================================================================= run 10: daily, clean
    r10, calls = run("daily", TODAY + timedelta(days=7))
    assert r10.ok, _hard_failures(r10)
    assert calls == {"tickers": 1, "submissions": 3}
    assert _coverage(conn) == {cik: (TODAY + timedelta(days=7), None) for cik in (AAPL, XOM_NEW, XOM_OLD)}

    # ---- stability over the ten runs
    all_runs = (r1, r2, r3, r4, r5, r6, r7, r8, r9, r10)
    assert [_notes(conn, r.run_id)["relabelled"] for r in all_runs] == [unique_rows, 0, 0, 0, 1, 1, 0, 0, 0, 0]
    assert conn.execute("SELECT notes->>'relabelled' AS n FROM runs WHERE id = %s", (r10.run_id,)).fetchone()["n"] == "0"
    assert conn.execute("SELECT count(*) AS n FROM runs WHERE status = 'ok'").fetchone()["n"] == 10
    assert [r["id"] for r in conn.execute("SELECT id FROM runs WHERE checks_passed ORDER BY id").fetchall()] == [r1.run_id, r2.run_id, r9.run_id, r10.run_id]
    assert conn.execute("SELECT count(*) AS n FROM ticker_cik_map").fetchone()["n"] == 20
    assert conn.execute("SELECT count(*) AS n FROM checks").fetchone()["n"] == 10 * len(CHECK_NAMES)
    assert conn.execute("SELECT count(*) AS n FROM filings").fetchone()["n"] == unique_rows
    assert conn.execute("SELECT count(*) AS n FROM sec_labels").fetchone()["n"] == len(xbrl_reports)
    assert _url_kinds(client) == {"tickers": 10, "submissions": 30, "history": 1, "companyfacts": 3}
    # the tables of this test live in the test schema only
    assert conn.execute("SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = %s AND table_name = 'filings'", (name,)).fetchone()["n"] == 1


# ----------------------------------------------------------------------------- the coverage watermark

def test_coverage_gap_is_persistent_until_a_history_read(schema, tmp_path: Path):
    name, conn = schema
    pages = all_fixtures()
    client = FakeEdgarClient(copy.deepcopy(pages), companyfacts=agreeing_facts(pages))
    names = {cik: add_history(client, cik) for cik in (AAPL, XOM_NEW, XOM_OLD)}
    settings = make_settings(name, tmp_path, ["AAPL", "XOM"], XOM_OVERRIDE_TOML + CORRECTION_TOML)
    day = lambda n: TODAY + timedelta(days=n)  # noqa: E731

    # backfill: every filer's history is read once; the watermark starts today
    r1 = daily_run(settings, mode="backfill", client=client, today=day(0))
    assert r1.ok, _hard_failures(r1)
    assert sorted(_history_urls(client)) == sorted(names.values())
    assert _coverage(conn) == {cik: (day(0), None) for cik in (AAPL, XOM_NEW, XOM_OLD)}
    assert conn.execute("SELECT count(*) AS n FROM filers WHERE backfill_completed_at IS NOT NULL").fetchone()["n"] == 3

    # daily: the pages overlap the watermark, so it advances
    r2 = daily_run(settings, mode="daily", client=client, today=day(1))
    assert r2.ok and _checks(conn, r2.run_id)["history_gap"]["summary"] == NO_GAP
    assert _coverage(conn) == {cik: (day(1), None) for cik in (AAPL, XOM_NEW, XOM_OLD)}

    # the watermark of one filer is older than its recent page: a gap is recorded, the watermark stays
    conn.execute("UPDATE filers SET coverage_through = %s WHERE cik = %s", (date(2026, 1, 1), XOM_NEW))
    r3 = daily_run(settings, mode="daily", client=client, today=day(2))
    assert r3.status == "ok" and _hard_failures(r3) == ["history_gap"]
    c3 = _checks(conn, r3.run_id)["history_gap"]
    assert c3["detail"] == {"items": [{"key": str(XOM_NEW), "ticker": "XOM", "cik": XOM_NEW, "gap_since": str(day(2)),
                                       "recent_page_starts": str(_min_filing_date(pages[XOM_NEW]))}]}
    assert c3["summary"].endswith(GAP_REMEDY)
    assert _coverage(conn) == {AAPL: (day(2), None), XOM_OLD: (day(2), None), XOM_NEW: (date(2026, 1, 1), day(2))}

    # another daily: still failing, same gap date
    r4 = daily_run(settings, mode="daily", client=client, today=day(3))
    assert _hard_failures(r4) == ["history_gap"]
    assert _checks(conn, r4.run_id)["history_gap"]["detail"]["items"][0]["gap_since"] == str(day(2))
    assert _coverage(conn)[XOM_NEW] == (date(2026, 1, 1), day(2))
    assert _url_kinds(client)["history"] == 3

    # a plain backfill (not --full) re-reads the history of the gapped filer only, and clears the gap
    r5 = daily_run(settings, mode="backfill", client=client, today=day(4))
    assert r5.ok, _hard_failures(r5)
    assert _history_urls(client)[3:] == [names[XOM_NEW]]
    assert _coverage(conn) == {cik: (day(4), None) for cik in (AAPL, XOM_NEW, XOM_OLD)}
    assert _checks(conn, r5.run_id)["history_gap"]["summary"] == NO_GAP

    # daily again: passes, the watermark advances
    r6 = daily_run(settings, mode="daily", client=client, today=day(5))
    assert r6.ok and _coverage(conn) == {cik: (day(5), None) for cik in (AAPL, XOM_NEW, XOM_OLD)}
    assert _url_kinds(client)["history"] == 4

    # --full re-reads every filer's history whether or not it has a gap
    r7 = daily_run(settings, mode="backfill", client=client, today=day(6), full=True)
    assert r7.ok and sorted(_history_urls(client)[4:]) == sorted(names.values())
    assert _coverage(conn) == {cik: (day(6), None) for cik in (AAPL, XOM_NEW, XOM_OLD)}


def test_a_first_daily_run_loads_the_whole_history(schema, tmp_path: Path):
    """The README's one command on an empty database, then a company added later. Until 2026-11-08 a daily run read
    recent pages only: on an empty database it ended RESULT: OK with 557 of 1,800 filings and no Apple 2009."""
    name, conn = schema
    pages = all_fixtures()
    client = FakeEdgarClient(copy.deepcopy(pages), companyfacts=agreeing_facts(pages))
    names = {cik: add_history(client, cik) for cik in (AAPL, XOM_NEW, XOM_OLD)}
    older_report = {"accession": "0000320193-91-000099", "form": "10-K", "filed": date(1991, 12, 20), "period": date(1991, 9, 27), "xbrl": 0}
    client.older_pages[names[AAPL]] = synthetic_page(AAPL, "history", "H", "0926", [older_report])["filings"]["recent"]

    # the first run ever is a plain daily run: the history is read and the old report answers
    settings = make_settings(name, tmp_path, ["AAPL"], CORRECTION_TOML)
    first = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert first.ok, _hard_failures(first)
    assert first.mode == "daily"
    assert _history_urls(client) == [names[AAPL]]
    assert [row["form"] for row in report.answer(conn, "AAPL", 1991, "FY")] == ["10-K"]

    # the second run reads no history again
    second = daily_run(settings, mode="daily", client=client, today=TODAY + timedelta(days=1))
    assert second.ok, _hard_failures(second)
    assert second.filings_inserted == 0
    assert _history_urls(client) == [names[AAPL]]

    # a company added later: the next daily run reads its history and nobody else's, predecessor CIK included
    settings = make_settings(name, tmp_path, ["AAPL", "XOM"], CORRECTION_TOML + XOM_OVERRIDE_TOML)
    third = daily_run(settings, mode="daily", client=client, today=TODAY + timedelta(days=2))
    assert third.ok, _hard_failures(third)
    assert sorted(_history_urls(client)[1:]) == sorted([names[XOM_NEW], names[XOM_OLD]])
    assert conn.execute("SELECT count(*) AS n FROM filers WHERE backfill_completed_at IS NULL").fetchone()["n"] == 0


@pytest.mark.parametrize("bad_page", [{}, None], ids=["empty", "http-404"])
def test_a_history_page_that_fails_leaves_the_filer_unread_until_it_is_served(schema, tmp_path: Path, bad_page):
    """Until 2026-11-08 an empty history page counted as read: the filer was marked complete and never read again."""
    name, conn = schema
    pages = all_fixtures()
    client = FakeEdgarClient(copy.deepcopy(pages), companyfacts=agreeing_facts(pages))
    history = add_history(client, AAPL)
    good_page = client.older_pages[history]
    settings = make_settings(name, tmp_path, ["AAPL"], CORRECTION_TOML)

    def serve(page):
        client.older_pages.pop(history, None)
        if page is not None:
            client.older_pages[history] = page

    def history_read() -> int:
        return conn.execute("SELECT count(*) AS n FROM filers WHERE backfill_completed_at IS NOT NULL").fetchone()["n"]

    serve(bad_page)
    failed = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert not failed.ok and AAPL in failed.fetch_errors and "run_complete" in _hard_failures(failed)
    assert history_read() == 0

    # the page is back: the next plain daily run reads the history and the run is clean
    serve(good_page)
    repaired = daily_run(settings, mode="daily", client=client, today=TODAY + timedelta(days=1))
    assert repaired.ok, _hard_failures(repaired)
    assert history_read() == 1

    # a database from before the first-read rule can hold a never-read filer with a gap: the gap survives a failed read
    conn.execute("UPDATE filers SET backfill_completed_at = NULL, coverage_gap_since = %s WHERE cik = %s", (TODAY, AAPL))
    serve(bad_page)
    assert not daily_run(settings, mode="daily", client=client, today=TODAY + timedelta(days=2)).ok
    assert conn.execute("SELECT coverage_gap_since FROM filers WHERE cik = %s", (AAPL,)).fetchone()["coverage_gap_since"] == TODAY


def test_advance_coverage_rules(schema):
    _, conn = schema
    run_id = conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]
    page = load_fixture(FIXTURE_FILES["WMT"])
    cik = int(page["cik"])
    client = FakeEdgarClient({cik: page})
    start = _min_filing_date(page)

    def coverage():
        return conn.execute("SELECT coverage_through, coverage_gap_since, backfill_completed_at FROM filers WHERE cik = %s", (cik,)).fetchone()

    # a filer's first run reads its history even when it is a daily run: the watermark starts today
    first = loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=date(2026, 11, 1))
    assert first.history_fetched is True
    row = coverage()
    assert (row["coverage_through"], row["coverage_gap_since"]) == (date(2026, 11, 1), None) and row["backfill_completed_at"] is not None
    # the page overlaps the watermark (its oldest filing is older than it): the watermark advances, nothing more is read
    assert loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=date(2026, 11, 2)).history_fetched is False
    assert coverage()["coverage_through"] == date(2026, 11, 2)
    # a page that starts after the watermark: the gap is recorded with today's date, the watermark stays
    conn.execute("UPDATE filers SET coverage_through = %s WHERE cik = %s", (start - timedelta(days=1), cik))
    loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=date(2026, 11, 3))
    row = coverage()
    assert (row["coverage_through"], row["coverage_gap_since"]) == (start - timedelta(days=1), date(2026, 11, 3))
    loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=date(2026, 11, 4))
    assert coverage()["coverage_gap_since"] == date(2026, 11, 3)                       # the first date is kept
    # a watermark equal to the page start overlaps it
    conn.execute("UPDATE filers SET coverage_through = %s, coverage_gap_since = NULL WHERE cik = %s", (start, cik))
    loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=date(2026, 11, 5))
    row = coverage()
    assert (row["coverage_through"], row["coverage_gap_since"]) == (date(2026, 11, 5), None)
    # a daily run with a gap on record does not read history and does not clear the gap
    conn.execute("UPDATE filers SET coverage_through = %s, coverage_gap_since = %s WHERE cik = %s", (date(2000, 1, 1), date(2026, 11, 3), cik))
    assert loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=date(2026, 11, 6)).history_fetched is False
    assert coverage()["coverage_gap_since"] == date(2026, 11, 3)
    # a backfill reads it: the watermark moves to today and the gap clears
    done = loader.ingest_filer(conn, client, run_id, cik, backfill=True, overrides_fye=None, today=date(2026, 11, 7))
    assert done.history_fetched is True
    row = coverage()
    assert (row["coverage_through"], row["coverage_gap_since"]) == (date(2026, 11, 7), None) and row["backfill_completed_at"] is not None
    assert _url_kinds(client)["history"] == 0      # the fixture lists no older pages: the read is the decision, not a request
    # the backfill is done, yet a gap on record makes the next backfill read history again
    conn.execute("UPDATE filers SET coverage_gap_since = %s WHERE cik = %s", (date(2026, 11, 8), cik))
    assert loader.ingest_filer(conn, client, run_id, cik, backfill=True, overrides_fye=None, today=date(2026, 11, 9)).history_fetched is True
    assert coverage()["coverage_gap_since"] is None
    assert loader.ingest_filer(conn, client, run_id, cik, backfill=True, overrides_fye=None, today=date(2026, 11, 10)).history_fetched is False
    # a database from before the first-read rule can hold a never-read filer with a gap: its first read is the repair
    conn.execute("UPDATE filers SET backfill_completed_at = NULL, coverage_gap_since = %s WHERE cik = %s", (date(2026, 11, 3), cik))
    assert loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=date(2026, 11, 11)).history_fetched is True
    assert coverage()["coverage_gap_since"] is None


# ----------------------------------------------------------------------------- the upsert's ownership rule

def _filing_row(accession: str, cik: int, **overrides) -> dict:
    row = {
        "accession_number": accession, "cik": cik, "form": "10-K", "family": "annual", "is_amendment": False,
        "is_transition": False, "filing_date": date(2026, 2, 2), "report_date": date(2025, 12, 31),
        "acceptance_datetime": datetime(2026, 2, 2, 21, 0, tzinfo=timezone.utc), "act": "34", "file_number": f"001-{cik:05d}",
        "film_number": "26123", "primary_document": "a.htm", "primary_doc_description": "10-K",
        "primary_document_url": document_url(cik, accession, "a.htm"), "is_xbrl": True, "is_inline_xbrl": True, "size_bytes": 10,
    }
    row.update(overrides)
    return row


def test_upsert_keeps_the_first_registrants_metadata_and_never_erases_with_null(schema):
    _, conn = schema
    run_id = conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]
    run2 = conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]
    conn.execute("INSERT INTO filers (cik) VALUES (1), (2)")
    acc = "0000000001-26-000001"
    first = _filing_row(acc, 1)

    assert loader.upsert_filings(conn, [first], run_id) == (1, 0)
    assert loader.upsert_filings(conn, [first], run_id) == (0, 1)
    conn.commit()

    def stored() -> dict:
        return conn.execute(
            "SELECT cik, form, family, is_amendment, filing_date, report_date, act, file_number, film_number, primary_document, "
            "primary_document_url, is_xbrl, size_bytes, also_filed_by_ciks, last_seen_run, not_seen_since FROM filings WHERE accession_number = %s",
            (acc,),
        ).fetchone()

    # another registrant lists the same accession with different metadata: only the bookkeeping moves
    other = _filing_row(acc, 2, form="10-K/A", is_amendment=True, filing_date=date(2026, 2, 3), report_date=date(2020, 1, 1),
                        act="33", film_number="99999", primary_document="b.htm", is_xbrl=False, size_bytes=99)
    conn.execute("UPDATE filings SET not_seen_since = %s WHERE accession_number = %s", (date(2026, 1, 1), acc))
    assert loader.upsert_filings(conn, [other], run2) == (0, 1)
    conn.commit()
    row = stored()
    assert row["cik"] == 1
    assert (row["form"], row["family"], row["is_amendment"], row["filing_date"], row["report_date"]) == ("10-K", "annual", False, date(2026, 2, 2), date(2025, 12, 31))
    assert (row["act"], row["file_number"], row["film_number"], row["primary_document"]) == ("34", "001-00001", "26123", "a.htm")
    assert row["primary_document_url"] == document_url(1, acc, "a.htm")
    assert row["is_xbrl"] is True and row["size_bytes"] == 10
    assert row["also_filed_by_ciks"] == [2]
    assert row["last_seen_run"] == run2 and row["not_seen_since"] is None
    # listing it again adds nothing
    loader.upsert_filings(conn, [other], run2)
    conn.commit()
    assert stored()["also_filed_by_ciks"] == [2]

    # the owning registrant's page with blanks: a NULL never erases a value
    blanks = _filing_row(acc, 1, report_date=None, acceptance_datetime=None, act=None, file_number=None, film_number=None,
                         primary_document=None, primary_doc_description=None, primary_document_url=None,
                         is_xbrl=None, is_inline_xbrl=None, size_bytes=None)
    assert loader.upsert_filings(conn, [blanks], run2) == (0, 1)
    conn.commit()
    row = stored()
    assert (row["report_date"], row["act"], row["file_number"], row["film_number"], row["primary_document"]) == (date(2025, 12, 31), "34", "001-00001", "26123", "a.htm")
    assert row["primary_document_url"] == document_url(1, acc, "a.htm") and row["is_xbrl"] is True and row["size_bytes"] == 10

    # the owning registrant's page with new values: taken, including the non-nullable form fields
    changed = _filing_row(acc, 1, form="10-K/A", is_amendment=True, file_number="001-00009", report_date=date(2025, 12, 27), size_bytes=11,
                          primary_document="c.htm", primary_document_url=document_url(1, acc, "c.htm"))
    assert loader.upsert_filings(conn, [changed], run2) == (0, 1)
    conn.commit()
    row = stored()
    assert (row["form"], row["is_amendment"], row["file_number"], row["report_date"], row["size_bytes"]) == ("10-K/A", True, "001-00009", date(2025, 12, 27), 11)
    assert (row["primary_document"], row["primary_document_url"]) == ("c.htm", document_url(1, acc, "c.htm"))
    assert row["cik"] == 1 and row["also_filed_by_ciks"] == [2]
    assert conn.execute("SELECT count(*) AS n FROM filings").fetchone()["n"] == 1


def test_blank_primary_document_on_a_later_page_keeps_the_document_url(schema):
    """The URL follows the document: a page that names no document keeps the URL of the stored document."""
    _, conn = schema
    run_id = conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]
    conn.execute("INSERT INTO filers (cik) VALUES (1)")
    acc = "0000000001-26-000001"
    loader.upsert_filings(conn, [_filing_row(acc, 1)], run_id)
    page = {"accessionNumber": [acc], "form": ["10-K"], "filingDate": ["2026-02-02"], "reportDate": ["2025-12-31"], "primaryDocument": [""]}
    loader.upsert_filings(conn, loader.parse_page(1, page), run_id)
    conn.commit()
    row = conn.execute("SELECT primary_document, primary_document_url FROM filings WHERE accession_number = %s", (acc,)).fetchone()
    assert row == {"primary_document": "a.htm", "primary_document_url": document_url(1, acc, "a.htm")}


# ----------------------------------------------------------------------------- backfill reads history once

def test_backfill_reads_the_older_pages_once_per_filer(schema):
    _, conn = schema
    run_id = conn.execute("INSERT INTO runs (mode) VALUES ('backfill') RETURNING id").fetchone()["id"]
    page = load_fixture(FIXTURE_FILES["WMT"])
    cik = int(page["cik"])
    older_name = "CIK0000104169-submissions-001.json"
    older = {
        "accessionNumber": ["0000104169-12-000001"], "filingDate": ["2012-03-27"], "reportDate": ["2012-01-31"],
        "acceptanceDateTime": ["2012-03-27T20:00:00.000Z"], "act": ["34"], "form": ["10-K"], "fileNumber": ["001-06991"],
        "filmNumber": ["12"], "items": [""], "core_type": [""], "size": [1], "isXBRL": [1], "isInlineXBRL": [0],
        "isXBRLNumeric": [1], "primaryDocument": ["wmt-20120131.htm"], "primaryDocDescription": ["10-K"],
    }
    page["filings"]["files"] = [{"name": older_name, "filingCount": 1, "filingFrom": "2012-03-27", "filingTo": "2012-03-27"}]
    client = FakeEdgarClient({cik: page}, older_pages={older_name: older})
    recent_rows = len(_tracked(page))

    first = loader.ingest_filer(conn, client, run_id, cik, backfill=True, overrides_fye=None, today=TODAY)
    assert (first.pages, first.rows, first.inserted, first.updated) == (2, recent_rows + 1, recent_rows + 1, 0)
    assert first.history_fetched is True
    assert first.recent_window_start == _min_filing_date(page)          # the recent page only, not the history
    assert client.stats.urls == [SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json"), SUBMISSIONS_URL.format(name=older_name)]
    filer = conn.execute("SELECT recent_window_start, backfill_completed_at, coverage_through, coverage_gap_since FROM filers WHERE cik = %s", (cik,)).fetchone()
    assert filer == {"recent_window_start": _min_filing_date(page), "backfill_completed_at": filer["backfill_completed_at"], "coverage_through": TODAY, "coverage_gap_since": None}
    assert filer["backfill_completed_at"] is not None
    assert conn.execute("SELECT count(*) AS n FROM source_snapshots WHERE cik = %s", (cik,)).fetchone()["n"] == 2

    # a second backfill skips the history: it was read once and nothing there changes
    second = loader.ingest_filer(conn, client, run_id, cik, backfill=True, overrides_fye=None, today=TODAY)
    assert (second.pages, second.rows, second.inserted, second.updated) == (1, recent_rows, 0, recent_rows)
    assert second.history_fetched is False
    assert client.stats.requests == 3
    # the 2012 10-K is outside the recent window, so not fetching it does not make it "missing"
    assert second.newly_missing == [] and conn.execute("SELECT count(*) AS n FROM filings WHERE not_seen_since IS NOT NULL").fetchone()["n"] == 0

    # with a coverage gap on record a plain backfill reads the history again and clears the gap
    conn.execute("UPDATE filers SET coverage_gap_since = %s WHERE cik = %s", (date(2026, 10, 1), cik))
    third = loader.ingest_filer(conn, client, run_id, cik, backfill=True, overrides_fye=None, today=TODAY + timedelta(days=1))
    assert (third.pages, third.history_fetched, third.inserted, third.updated) == (2, True, 0, recent_rows + 1)
    assert client.stats.requests == 5
    assert conn.execute("SELECT coverage_through, coverage_gap_since FROM filers WHERE cik = %s", (cik,)).fetchone() == {"coverage_through": TODAY + timedelta(days=1), "coverage_gap_since": None}

    # full=True re-reads everything regardless
    fourth = loader.ingest_filer(conn, client, run_id, cik, backfill=True, overrides_fye=None, today=TODAY, full=True)
    assert (fourth.pages, fourth.history_fetched) == (2, True)
    assert client.stats.requests == 7
    # a daily fetch never touches the history pages, gap or not
    conn.execute("UPDATE filers SET coverage_gap_since = %s WHERE cik = %s", (date(2026, 10, 1), cik))
    daily = loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=TODAY, full=True)
    assert (daily.pages, daily.history_fetched) == (1, False)
    assert client.stats.requests == 8
    assert conn.execute("SELECT coverage_gap_since FROM filers WHERE cik = %s", (cik,)).fetchone()["coverage_gap_since"] == date(2026, 10, 1)
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE cik = %s", (cik,)).fetchone()["n"] == recent_rows + 1

    # a page that fails validation is an EdgarError before anything is written for that filer
    client.pages[cik]["cik"] = "0000000042"
    with pytest.raises(EdgarError, match="says cik='0000000042'"):
        loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=TODAY)
    conn.rollback()


# ----------------------------------------------------------------------------- co-filed accessions and "missing"

def test_seen_this_run_protects_co_filed_accessions(schema):
    _, conn = schema
    shared_acc, own_acc, own2_acc = "0000000001-26-000010", "0000000001-26-000020", "0000000002-26-000030"
    shared = {"accession": shared_acc, "form": "10-Q", "filed": date(2026, 8, 3), "period": date(2026, 6, 30), "xbrl": 0}
    own = {"accession": own_acc, "form": "10-K", "filed": date(2026, 2, 20), "period": date(2025, 12, 31), "xbrl": 0}
    own2 = {"accession": own2_acc, "form": "10-Q", "filed": date(2026, 5, 4), "period": date(2026, 3, 31), "xbrl": 0}
    older = {"accession": "0000000001-25-000005", "form": "10-Q", "filed": date(2025, 11, 3), "period": date(2025, 9, 30), "xbrl": 0}
    page1 = synthetic_page(1, "Holding Corp", "HLD", "1231", [shared, own, older])   # `older` keeps the page's window wide
    page2 = synthetic_page(2, "Operating Corp", "OPR", "1231", [shared, own2])
    client = FakeEdgarClient({1: copy.deepcopy(page1), 2: copy.deepcopy(page2)})   # page1/page2 stay pristine

    def new_run() -> int:
        return conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]

    def ingest(run_id: int, cik: int, seen: set[str] | None):
        return loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=TODAY, seen_this_run=seen)

    def flag(acc: str):
        return conn.execute("SELECT not_seen_since FROM filings WHERE accession_number = %s", (acc,)).fetchone()["not_seen_since"]

    run1 = new_run()
    seen: set[str] = set()
    ingest(run1, 1, seen)
    ingest(run1, 2, seen)
    assert seen == {shared_acc, own_acc, own2_acc, older["accession"]}
    assert conn.execute("SELECT cik, also_filed_by_ciks FROM filings WHERE accession_number = %s", (shared_acc,)).fetchone() == {"cik": 1, "also_filed_by_ciks": [2]}

    # the holding company's page drops the co-filed 10-Q while the operating company's still lists it:
    # when the other page came first in the run, the shared set says the filing is still on the feed
    _remove_accession(client.pages[1], shared_acc)
    run2 = new_run()
    seen = set()
    ingest(run2, 2, seen)
    assert ingest(run2, 1, seen).newly_missing == []
    assert flag(shared_acc) is None

    # without the shared set the same page flags it; the other registrant's upsert then lifts the flag again
    run3 = new_run()
    assert ingest(run3, 1, None).newly_missing == [shared_acc]
    assert flag(shared_acc) == TODAY
    ingest(run3, 2, None)
    assert conn.execute("SELECT not_seen_since, last_seen_run FROM filings WHERE accession_number = %s", (shared_acc,)).fetchone() == {"not_seen_since": None, "last_seen_run": run3}

    # the co-registrant's page is watched too: when the operating company's page drops the shared filing and the
    # holding company's page came first, the shared set protects it...
    client.pages[1] = copy.deepcopy(page1)
    _remove_accession(client.pages[2], shared_acc)
    run4 = new_run()
    seen = set()
    ingest(run4, 1, seen)
    assert ingest(run4, 2, seen).newly_missing == []
    assert flag(shared_acc) is None
    # ...and without it the filing is flagged through also_filed_by_ciks, until the owner's page lists it again
    run5 = new_run()
    assert ingest(run5, 2, None).newly_missing == [shared_acc]
    assert flag(shared_acc) == TODAY
    ingest(run5, 1, None)
    assert flag(shared_acc) is None

    # a filing of the holding company's own that really vanished is still caught, shared set or not
    _remove_accession(client.pages[1], own_acc)
    run6 = new_run()
    seen = set()
    ingest(run6, 2, seen)
    assert ingest(run6, 1, seen).newly_missing == [own_acc]
    assert flag(own_acc) == TODAY


# ----------------------------------------------------------------------------- snapshots: a repeated page

def test_snapshot_with_the_same_hash_only_records_the_run(schema):
    _, conn = schema
    page = load_fixture(FIXTURE_FILES["WMT"])
    cik = int(page["cik"])
    client = FakeEdgarClient({cik: page})
    run1 = conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]
    first = loader.ingest_filer(conn, client, run1, cik, backfill=False, overrides_fye=None, today=TODAY)
    assert first.snapshot_changed is True
    # whatever the stored body holds is left alone when the same content comes back: only the run that saw it moves
    conn.execute("UPDATE source_snapshots SET body = body || '{\"marker\": true}'::jsonb WHERE cik = %s", (cik,))
    run2 = conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]
    again = loader.ingest_filer(conn, client, run2, cik, backfill=False, overrides_fye=None, today=TODAY)
    assert again.snapshot_changed is False
    rows = conn.execute("SELECT run_id, last_seen_run, body FROM source_snapshots WHERE cik = %s", (cik,)).fetchall()
    assert len(rows) == 1
    assert (rows[0]["run_id"], rows[0]["last_seen_run"]) == (run1, run2)
    assert rows[0]["body"]["marker"] is True and rows[0]["body"]["cik"] == page["cik"]


# ----------------------------------------------------------------------------- SEC labels: refresh and judgement

def test_daily_run_refreshes_sec_labels_and_judges_disagreements(schema, tmp_path: Path):
    name, conn = schema
    pages = all_fixtures()
    apple = pages[AAPL]
    fy2026 = _accession(apple, "10-K", "2026-09-26")      # filed 2026-11-03
    fy2025 = _accession(apple, "10-K", "2025-09-27")      # filed 2025-10-31
    q1_2026 = _accession(apple, "10-Q", "2025-12-27")     # filed 2026-01-xx
    # the SEC agrees with our rule on every XBRL report, except one convention and one real disagreement
    sec = agreeing_labels(apple)
    sec[fy2025] = (2024, "FY", "10-K")
    sec[q1_2026] = (2025, "Q1", "10-Q")
    assert sec[fy2026] == (2026, "FY", "10-K")
    client = FakeEdgarClient(copy.deepcopy(pages), companyfacts={AAPL: _companyfacts(sec)})
    conventions = """
[[naming_conventions]]
ticker = "aapl"
sec_year_offset = -1
until = 2025-12-31
note = "pretend Apple named years by their start until the end of 2025 (test data)"
"""
    settings = make_settings(name, tmp_path, ["AAPL"], conventions + CORRECTION_TOML)

    r1 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert r1.status == "ok" and r1.filings_inserted > 0
    assert _url_kinds(client) == {"tickers": 1, "submissions": 1, "companyfacts": 1}     # new reports -> labels refreshed
    assert conn.execute("SELECT count(*) AS n FROM sec_labels").fetchone()["n"] == len(sec)
    assert conn.execute("SELECT fiscal_year, fiscal_period, form, source FROM sec_labels WHERE accession_number = %s", (fy2025,)).fetchone() == {
        "fiscal_year": 2024, "fiscal_period": "FY", "form": "10-K", "source": "companyfacts"}
    c1 = _checks(conn, r1.run_id)
    assert c1["sec_labels_refreshed"]["passed"] is True and c1["sec_labels_refreshed"]["detail"] == {"items": [], "refreshed": [AAPL]}
    assert c1["news"]["summary"].endswith("; SEC labels refreshed for 1 filer(s)")
    assert c1["news"]["detail"]["sec_labels_refreshed"] == [AAPL] and c1["news"]["detail"]["sec_label_refresh_errors"] == {}
    match = c1["labels_match_sec"]
    assert match["severity"] == "hard" and match["passed"] is False
    assert _hard_failures(r1) == ["labels_match_sec"]
    # the 2025 10-K is one year below ours, same period, filed before `until`: a documented convention;
    # the 2026 Q1 10-Q is one year below too, but filed after `until`: a real disagreement
    assert match["detail"]["items"] == [{"key": q1_2026, "kind": "disagreement", "ticker": "AAPL", "form": "10-Q",
                                         "report_date": "2025-12-27", "ours": "2026 Q1", "sec": "2025 Q1"}]
    assert match["detail"]["naming_convention_counts"] == {"AAPL": 1}
    assert match["detail"]["compared"] == len(sec)
    assert match["detail"]["pending_sec_label"] == 0 and match["detail"]["older_without_sec_label"] == 0
    assert match["summary"] == (
        f"1 real disagreement(s) with the SEC's XBRL label on {len(sec)} filings compared; 0 XBRL filing(s) older than 21 days "
        f"still without a SEC label; 1 are documented naming conventions (AAPL 1); 0 recent filing(s) awaiting one"
    )
    # the reports view carries the SEC label next to ours
    row = report.answer(conn, "AAPL", 2025, "FY")[0]
    assert (row["fiscal_year"], row["sec_fiscal_year"], row["sec_fiscal_period"]) == (2025, 2024, "FY")
    assert report.answer(conn, "AAPL", 2009, "FY")[0]["sec_fiscal_year"] == 2009
    assert report.answer(conn, "AAPL", 2005, "FY")[0]["sec_fiscal_year"] is None      # pre-XBRL: no label exists

    # verify-labels: the raw count includes the convention; the morning check is what tells them apart
    out = verify.verify_labels(conn, client)
    assert out == {"labels_stored": len(sec), "compared": len(sec), "raw_disagreements": 2, "filers_without_xbrl": []}
    assert verify.refresh_sec_labels(conn, client, [999]) == {999: 0}      # no companyfacts: nothing stored, reported as 0
    assert _url_kinds(client)["companyfacts"] == 3

    # a person accepts the real disagreement: the check turns into information
    settings.overrides_file.write_text(conventions + CORRECTION_TOML + f'\n[[accepted_findings]]\ncheck = "labels_match_sec"\nkey = "{q1_2026}"\nnote = "read the cover page"\n', encoding="utf-8")
    r2 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert r2.ok and r2.filings_inserted == 0
    assert _url_kinds(client)["companyfacts"] == 3         # every XBRL report has its label: nothing to retry
    match2 = _checks(conn, r2.run_id)["labels_match_sec"]
    assert match2["severity"] == "info" and match2["passed"] is True
    assert match2["summary"].startswith(f"1 real disagreement(s) with the SEC's XBRL label on {len(sec)} filings compared")
    assert match2["summary"].endswith("(all 1 accepted in overrides.toml)")
    assert match2["detail"]["accepted"][0]["key"] == q1_2026 and match2["detail"]["items"] == []

    # without the convention entry the 2025 10-K is a real disagreement too
    settings.overrides_file.write_text(CORRECTION_TOML, encoding="utf-8")
    r3 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert _hard_failures(r3) == ["labels_match_sec"]
    match3 = _checks(conn, r3.run_id)["labels_match_sec"]
    assert match3["passed"] is False and [(i["key"], i["kind"]) for i in match3["detail"]["items"]] == [(fy2025, "disagreement"), (q1_2026, "disagreement")]
    assert match3["detail"]["naming_convention_counts"] == {}


YOUNG = {"accession": "0000000007-26-000003", "form": "10-K", "filed": TODAY - timedelta(days=1), "period": date(2026, 9, 26), "xbrl": 1}
OLD = {"accession": "0000000007-25-000002", "form": "10-K", "filed": TODAY - timedelta(days=100), "period": date(2025, 9, 27), "xbrl": 1}
ANCIENT = {"accession": "0000000007-05-000001", "form": "10-K", "filed": date(2005, 12, 1), "period": date(2005, 9, 24), "xbrl": 0}
TWO_A = {"accession": "0000000008-26-000001", "form": "10-K", "filed": date(2026, 6, 1), "period": date(2025, 12, 31), "xbrl": 1}
TWO_B = {"accession": "0000000008-26-000002", "form": "10-Q", "filed": date(2026, 7, 1), "period": date(2026, 3, 31), "xbrl": 1}


def test_unlabelled_xbrl_reports_are_findings_and_are_retried_every_run(schema, tmp_path: Path):
    name, conn = schema
    client = FakeEdgarClient(
        {7: synthetic_page(7, "Young Filer Inc.", "YNG", "0926", [YOUNG, OLD, ANCIENT]),
         8: synthetic_page(8, "Two Filer Inc.", "TWO", "1231", [TWO_A, TWO_B])},
        companyfacts={7: _companyfacts({}), 8: _companyfacts({})},     # XBRL exists, but no facts yet
    )
    settings = make_settings(name, tmp_path, ["YNG", "TWO"], "")

    r1 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert r1.status == "ok" and r1.filings_inserted == 5
    assert _url_kinds(client)["companyfacts"] == 2                        # new reports: refreshed
    c1 = _checks(conn, r1.run_id)
    assert c1["sec_labels_refreshed"]["summary"] == "SEC labels refreshed for 2 filer(s)"
    match1 = c1["labels_match_sec"]
    assert match1["severity"] == "hard" and match1["passed"] is False and _hard_failures(r1) == ["labels_match_sec"]
    # with no label loaded at all, the XBRL filings older than 21 days are findings; the one filed yesterday is pending
    assert [(i["key"], i["kind"], i["ticker"], i["filed"]) for i in match1["detail"]["items"]] == [
        (TWO_A["accession"], "no_sec_label", "TWO", str(TWO_A["filed"])),
        (TWO_B["accession"], "no_sec_label", "TWO", str(TWO_B["filed"])),
        (OLD["accession"], "no_sec_label", "YNG", str(OLD["filed"])),
    ]
    assert match1["detail"] | {"items": None} == {"items": None, "compared": 0, "naming_convention_counts": {}, "pending_sec_label": 1, "older_without_sec_label": 3}
    assert match1["summary"] == ("0 real disagreement(s) with the SEC's XBRL label on 0 filings compared; 3 XBRL filing(s) older than 21 days "
                                 "still without a SEC label; 1 recent filing(s) awaiting one")
    # the pre-XBRL filing is never expected to have one
    assert ANCIENT["accession"] not in {i["key"] for i in match1["detail"]["items"]}

    # the run marked each filer as attempted before asking (7 first, then 8): the queue serves the oldest attempt first
    attempted = {r["cik"]: r["sec_labels_attempted_at"] for r in conn.execute("SELECT cik, sec_labels_attempted_at FROM filers").fetchall()}
    assert attempted[7] is not None and attempted[8] is not None and attempted[7] < attempted[8]
    assert loader.ciks_awaiting_sec_labels(conn, [7, 8]) == [7, 8]
    assert loader.ciks_awaiting_sec_labels(conn, [7, 8], limit=1) == [7]
    assert loader.ciks_awaiting_sec_labels(conn, [7]) == [7]
    assert loader.ciks_awaiting_sec_labels(conn, [7], {OLD["accession"]}) == [7]                      # the young one still lacks a label
    assert loader.ciks_awaiting_sec_labels(conn, [7], {OLD["accession"], YOUNG["accession"]}) == []
    # an attempt sends the filer to the back of the queue; a filer never attempted goes to the front
    loader.mark_sec_labels_attempted(conn, [7])
    assert loader.ciks_awaiting_sec_labels(conn, [7, 8]) == [8, 7]
    conn.execute("UPDATE filers SET sec_labels_attempted_at = NULL WHERE cik = 7")
    assert loader.ciks_awaiting_sec_labels(conn, [7, 8]) == [7, 8]
    assert loader.ciks_awaiting_sec_labels(conn, [7, 8], limit=1) == [7]

    # no new reports, but unlabelled XBRL filings: the daily run asks companyfacts again
    client.facts[7] = _companyfacts({YOUNG["accession"]: (2026, "FY", "10-K")})
    r2 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert r2.filings_inserted == 0
    assert _url_kinds(client)["companyfacts"] == 4
    c2 = _checks(conn, r2.run_id)
    assert c2["sec_labels_refreshed"]["detail"]["refreshed"] == [7, 8]
    assert conn.execute("SELECT count(*) AS n FROM sec_labels").fetchone()["n"] == 1
    match2 = c2["labels_match_sec"]
    assert [i["key"] for i in match2["detail"]["items"]] == [TWO_A["accession"], TWO_B["accession"], OLD["accession"]]
    assert match2["detail"] | {"items": None} == {"items": None, "compared": 1, "naming_convention_counts": {}, "pending_sec_label": 0, "older_without_sec_label": 3}
    # with equal attempt ages the filer with more unlabelled reports comes first (8 has two, 7 has one left)
    conn.execute("UPDATE filers SET sec_labels_attempted_at = NULL WHERE cik IN (7, 8)")
    assert loader.ciks_awaiting_sec_labels(conn, [7, 8]) == [8, 7]
    conn.execute("UPDATE filers SET sec_labels_attempted_at = %s WHERE cik IN (7, 8)", (datetime(2026, 1, 1, tzinfo=timezone.utc),))
    assert loader.ciks_awaiting_sec_labels(conn, [7, 8]) == [8, 7]

    # no age bound: the 100-day-old filing keeps being retried
    r3 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert _url_kinds(client)["companyfacts"] == 6
    assert _checks(conn, r3.run_id)["sec_labels_refreshed"]["summary"] == "SEC labels refreshed for 2 filer(s)"

    # a person accepts the missing labels by accession: no finding, and no more refresh for those filers
    accepted = "".join(f'[[accepted_findings]]\ncheck = "labels_match_sec"\nkey = "{a}"\nnote = "no XBRL facts were ever published"\n'
                       for a in (OLD["accession"], TWO_A["accession"], TWO_B["accession"]))
    settings.overrides_file.write_text(accepted, encoding="utf-8")
    r4 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert r4.ok, _hard_failures(r4)
    assert _url_kinds(client)["companyfacts"] == 6
    c4 = _checks(conn, r4.run_id)
    assert c4["sec_labels_refreshed"]["summary"] == "SEC labels refreshed for 0 filer(s)"
    match4 = c4["labels_match_sec"]
    assert match4["severity"] == "info" and match4["summary"].endswith("(all 3 accepted in overrides.toml)")
    assert {i["kind"] for i in match4["detail"]["accepted"]} == {"no_sec_label"}


def test_sec_label_refresh_failure_is_a_hard_check_and_is_retried(schema, tmp_path: Path):
    name, conn = schema

    class Flaky(FakeEdgarClient):
        broken = True

        def companyfacts(self, cik: int):
            if self.broken:
                self.stats.requests += 1
                self.stats.urls.append(COMPANYFACTS_URL.format(cik=cik))
                raise EdgarError(f"companyfacts for {cik}: HTTP 503 after retries")
            return super().companyfacts(cik)

    client = Flaky({7: synthetic_page(7, "Young Filer Inc.", "YNG", "0926", [YOUNG, OLD, ANCIENT])},
                   companyfacts={7: _companyfacts({YOUNG["accession"]: (2026, "FY", "10-K")})})
    settings = make_settings(name, tmp_path, ["YNG"], "")
    r1 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert r1.status == "ok" and r1.ok is False
    assert _hard_failures(r1) == ["sec_labels_refreshed", "labels_match_sec"]
    c1 = _checks(conn, r1.run_id)
    assert c1["sec_labels_refreshed"]["summary"] == "SEC label refresh failed for 1 filer(s); retried automatically next run"
    assert c1["sec_labels_refreshed"]["detail"] == {"items": [{"key": "7", "cik": 7, "error": "companyfacts for 7: HTTP 503 after retries"}], "refreshed": []}
    assert c1["news"]["summary"].endswith("; SEC label refresh failed for 1 filer(s)")
    assert c1["news"]["detail"]["sec_label_refresh_errors"] == {"7": "companyfacts for 7: HTTP 503 after retries"}
    assert conn.execute("SELECT count(*) AS n FROM sec_labels").fetchone()["n"] == 0
    # the attempt is recorded before the answer comes back, failure or not
    assert conn.execute("SELECT sec_labels_attempted_at FROM filers WHERE cik = 7").fetchone()["sec_labels_attempted_at"] is not None

    # next run, EDGAR answers: the unlabelled filings make the run retry without new reports
    client.broken = False
    r2 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert r2.filings_inserted == 0
    assert _url_kinds(client)["companyfacts"] == 2
    c2 = _checks(conn, r2.run_id)
    assert c2["sec_labels_refreshed"]["passed"] is True and c2["sec_labels_refreshed"]["detail"]["refreshed"] == [7]
    assert conn.execute("SELECT count(*) AS n FROM sec_labels").fetchone()["n"] == 1
    assert _hard_failures(r2) == ["labels_match_sec"]      # the 100-day-old filing still has no label: a finding, not a failure to fetch
    assert [i["key"] for i in c2["labels_match_sec"]["detail"]["items"]] == [OLD["accession"]]


# ----------------------------------------------------------------------------- originals under two CIKs of one company

def _seed_filing(conn, run_id: int, cik: int, accession: str, form: str, report_date: date, fiscal_year: int, period: str,
                 also: list[int] | None = None) -> None:
    conn.execute(
        """
        INSERT INTO filings (accession_number, cik, form, family, is_amendment, filing_date, report_date, fiscal_year, fiscal_period,
                             label_method, first_seen_run, last_seen_run, also_filed_by_ciks)
        VALUES (%s, %s, %s, %s, false, %s, %s, %s, %s, 'rule', %s, %s, %s)
        """,
        (accession, cik, form, fiscal.family(form), report_date + timedelta(days=40), report_date, fiscal_year, period, run_id, run_id, also or []),
    )


def test_labels_unique_across_ciks_needs_two_accessions_and_two_registrants(schema):
    _, conn = schema
    run_id = conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]
    conn.execute("INSERT INTO companies (ticker) VALUES ('XOM'), ('OTH'), ('CO')")
    conn.execute("INSERT INTO company_ciks (ticker, cik, source, is_current) VALUES ('XOM', 1, 'sec_tickers', true), ('XOM', 2, 'override', false), ('OTH', 3, 'sec_tickers', true), ('CO', 4, 'sec_tickers', true)")
    conn.execute("INSERT INTO filers (cik, fiscal_year_end) VALUES (1, '1231'), (2, '1231'), (3, '1231'), (4, '1231'), (5, '1231')")
    # one combined filing listed by both CIKs: one accession, not a duplicate
    _seed_filing(conn, run_id, 1, "0000000002-26-000093", "10-Q", date(2026, 6, 30), 2026, "Q2", also=[2])
    assert checks.check_labels_unique_across_ciks(conn).passed is True
    assert checks.check_labels_unique(conn).passed is True

    # two different filings for the same period, one per CIK: reported, soft, with both CIKs and accessions
    _seed_filing(conn, run_id, 2, "0000000002-26-000094", "10-Q", date(2026, 6, 30), 2026, "Q2")
    out = checks.check_labels_unique_across_ciks(conn)
    assert out.severity == "soft" and out.passed is False
    assert out.detail["items"] == [{"key": "XOM/quarterly/2026/Q2", "ciks": [1, 2], "accessions": ["0000000002-26-000093", "0000000002-26-000094"]}]
    assert out.summary.startswith("1 period(s) reported as originals by two CIKs of the same company")
    assert checks.check_labels_unique(conn).passed is True        # per CIK there is still one original each

    # two originals under the same CIK are labels_unique's business, not this check's
    _seed_filing(conn, run_id, 3, "0000000003-26-000001", "10-K", date(2025, 12, 31), 2025, "FY")
    _seed_filing(conn, run_id, 3, "0000000003-26-000002", "10-K", date(2025, 12, 31), 2025, "FY")
    assert [i["key"] for i in checks.check_labels_unique_across_ciks(conn).detail["items"]] == ["XOM/quarterly/2026/Q2"]
    assert [i["key"] for i in checks.check_labels_unique(conn).detail["items"]] == ["OTH/3/annual/2025/FY"]

    # registrants are what count: a filing owned by a CIK outside the company, co-filed by the company's CIK, plus the
    # company's own filing for the period is one registrant (4) seen twice, not two...
    _seed_filing(conn, run_id, 5, "0000000005-26-000001", "10-K", date(2025, 12, 31), 2025, "FY", also=[4])
    _seed_filing(conn, run_id, 4, "0000000004-26-000001", "10-K", date(2025, 12, 31), 2025, "FY")
    assert [i["key"] for i in checks.check_labels_unique_across_ciks(conn).detail["items"]] == ["XOM/quarterly/2026/Q2"]
    # ...until the owning CIK becomes one of the company's CIKs too
    conn.execute("INSERT INTO company_ciks (ticker, cik, source, is_current) VALUES ('CO', 5, 'override', false)")
    items = checks.check_labels_unique_across_ciks(conn).detail["items"]
    assert items[0] == {"key": "CO/annual/2025/FY", "ciks": [4, 5], "accessions": ["0000000004-26-000001", "0000000005-26-000001"]}
    assert [i["key"] for i in items] == ["CO/annual/2025/FY", "XOM/quarterly/2026/Q2"]


# ----------------------------------------------------------------------------- overdue reports: NT grace, 52/53 weeks

def _seed_company(conn, ticker: str, cik: int, fye: str, category: str, filings: list[tuple[str, str, str]]) -> None:
    """filings: (form, report_date, filing_date)."""
    run_id = conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]
    conn.execute("INSERT INTO companies (ticker) VALUES (%s) ON CONFLICT DO NOTHING", (ticker,))
    conn.execute("INSERT INTO company_ciks (ticker, cik, source, is_current) VALUES (%s, %s, 'sec_tickers', true)", (ticker, cik))
    conn.execute("INSERT INTO filers (cik, name, entity_type, category, fiscal_year_end) VALUES (%s, %s, 'operating', %s, %s)", (cik, ticker, category, fye))
    for i, (form, report_date, filing_date) in enumerate(filings):
        conn.execute(
            """
            INSERT INTO filings (accession_number, cik, form, family, is_amendment, filing_date, report_date, fiscal_year, fiscal_period,
                                 label_method, first_seen_run, last_seen_run)
            VALUES (%s, %s, %s, %s, false, %s, %s, 2026, 'Q3', 'rule', %s, %s)
            """,
            (f"{cik:010d}-26-{i:06d}", cik, form, fiscal.family(form), filing_date, report_date, run_id, run_id),
        )


def test_overdue_reports_nt_grace_and_week_calendars(schema):
    _, conn = schema
    # Exxon-like calendar-year filer: last report Q3 2026, next is the 10-K for 2026-12-31 due 2027-03-01 (60 days, Monday)
    _seed_company(conn, "CAL", 1, "1231", "Large accelerated filer", [("10-Q", "2026-09-30", "2026-11-02")])
    # same, with a NT 10-K for the year end filed on the business day after the deadline: timely, 15 days of grace
    _seed_company(conn, "TIM", 2, "1231", "Large accelerated filer", [("10-Q", "2026-09-30", "2026-11-02"), ("NT 10-K", "2026-12-31", "2027-03-02")])
    # same, NT 10-K filed two days after the deadline: too late, no grace
    _seed_company(conn, "LAT", 3, "1231", "Large accelerated filer", [("10-Q", "2026-09-30", "2026-11-02"), ("NT 10-K", "2026-12-31", "2027-03-03")])
    # a NT 10-Q for the year-end period is the wrong kind of notice: no grace
    _seed_company(conn, "WRK", 4, "1231", "Large accelerated filer", [("10-Q", "2026-09-30", "2026-11-02"), ("NT 10-Q", "2026-12-31", "2027-03-01")])
    # Apple-like 52/53-week filer (year end 0926 is not a month end): next period = 13 weeks after the last report
    _seed_company(conn, "WKS", 5, "0926", "Large accelerated filer", [("10-Q", "2026-06-27", "2026-07-31")])
    _seed_company(conn, "WKQ", 6, "0926", "Large accelerated filer", [("10-Q", "2026-06-27", "2026-07-31"), ("10-K", "2026-09-26", "2026-11-03")])
    # a filer with no 10-Q on record is expected to file annual reports only
    _seed_company(conn, "ANN", 8, "0926", "Large accelerated filer", [("10-K", "2026-09-26", "2026-11-03")])
    # a 20-F filer called 'Large accelerated filer' by EDGAR: four months after the year end, next business day
    _seed_company(conn, "FPI", 7, "0331", "Large accelerated filer", [("20-F", "2026-03-31", "2026-06-10")])

    result = checks.check_overdue_reports(conn, date(2027, 3, 10))
    expected = {e["ticker"]: e for e in result.detail["expected_next"]}
    assert expected["CAL"]["key"] == "CAL/annual/2026-12-31" and expected["CAL"]["deadline"] == "2027-03-01"
    assert expected["CAL"]["late_notice"] is None and expected["CAL"]["notice_timely"] is False
    assert expected["TIM"]["deadline"] == "2027-03-16" and expected["TIM"]["late_notice"] == "2027-03-02" and expected["TIM"]["notice_timely"] is True
    assert expected["LAT"]["deadline"] == "2027-03-01" and expected["LAT"]["late_notice"] == "2027-03-03" and expected["LAT"]["notice_timely"] is False
    assert expected["WRK"]["deadline"] == "2027-03-01" and expected["WRK"]["late_notice"] is None
    assert expected["WKS"]["key"] == "WKS/annual/2026-09-26"          # 2026-06-27 + 13 weeks lands on the year end
    assert expected["WKS"]["deadline"] == str(checks.statutory_deadline(date(2026, 9, 26), "large_accelerated", "annual"))
    assert expected["WKQ"]["key"] == "WKQ/quarterly/2026-12-26" and expected["WKQ"]["deadline"] == "2027-02-04"
    assert expected["FPI"]["key"] == "FPI/annual/2027-03-31" and expected["FPI"]["filer_category"] == "foreign_private_issuer"
    assert expected["FPI"]["deadline"] == "2027-08-02"
    assert expected["ANN"]["key"] == "ANN/annual/2027-09-26" and expected["ANN"]["deadline"] == "2027-11-25"
    overdue = {i["ticker"]: i["days_overdue"] for i in result.detail["items"]}
    assert overdue == {"CAL": 9, "LAT": 9, "WRK": 9, "WKS": (date(2027, 3, 10) - date(2026, 11, 25)).days, "WKQ": 34}
    assert result.passed is False and result.severity == "soft"
    assert checks.check_overdue_reports(conn, date(2027, 2, 1)).detail["items"] == [{**expected["WKS"], "days_overdue": (date(2027, 2, 1) - date(2026, 11, 25)).days}]


# ----------------------------------------------------------------------------- reports moving from recent to archives

@pytest.mark.parametrize("report_still_listed", [True, False])
def test_recent_rollover_checks_only_relevant_archive_before_marking_missing(schema, report_still_listed):
    _, conn = schema
    cik = 1
    old = {"accession": "0000000001-25-000010", "form": "10-K", "filed": date(2025, 2, 20),
           "period": date(2024, 12, 31), "xbrl": 0}
    recent = {"accession": "0000000001-25-000020", "form": "10-Q", "filed": date(2025, 5, 5),
              "period": date(2025, 3, 31), "xbrl": 0}
    page = synthetic_page(cik, "Rollover Corp", "ROLL", "1231", [recent, old])
    client = FakeEdgarClient({cik: page})
    older_name = add_history(client, cik)

    def ingest(day):
        run_id = conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]
        return loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=day)

    first = ingest(TODAY)
    assert first.history_fetched and first.pages == 2
    completed = conn.execute("SELECT backfill_completed_at FROM filers WHERE cik = %s", (cik,)).fetchone()["backfill_completed_at"]
    older_descriptor = copy.deepcopy(page["filings"]["files"][0])
    _remove_accession(page, old["accession"])
    archive_name = "CIK0000000001-submissions-002.json"
    anchor = {"accession": "0000000001-25-000009", "form": "8-K", "filed": date(2025, 2, 19),
              "period": date(2025, 2, 19), "xbrl": 0}
    archived_rows = [anchor, old] if report_still_listed else [anchor]
    client.older_pages[archive_name] = synthetic_page(cik, "archive", "ROLL", "1231", archived_rows)["filings"]["recent"]
    # filingTo is deliberately a day too early, as observed on real SEC history descriptors.
    page["filings"]["files"] = [older_descriptor, {"name": archive_name, "filingCount": len(archived_rows),
                                                  "filingFrom": "2025-02-19", "filingTo": "2025-02-19"}]
    gap = date(2026, 10, 1)
    conn.execute("UPDATE filers SET coverage_gap_since = %s WHERE cik = %s", (gap, cik))

    second = ingest(TODAY + timedelta(days=1))
    assert second.pages == 2 and not second.history_fetched and second.inserted == 0
    assert _history_urls(client) == [older_name, archive_name]
    expected_missing = [] if report_still_listed else [old["accession"]]
    assert second.newly_missing == expected_missing
    assert conn.execute("SELECT not_seen_since FROM filings WHERE accession_number = %s", (old["accession"],)).fetchone()["not_seen_since"] == (
        None if report_still_listed else TODAY + timedelta(days=1)
    )
    assert conn.execute("SELECT backfill_completed_at, coverage_gap_since FROM filers WHERE cik = %s", (cik,)).fetchone() == {
        "backfill_completed_at": completed, "coverage_gap_since": gap,
    }

    if report_still_listed:
        # Once reconciled, an unchanged daily run needs only the recent page again.
        third = ingest(TODAY + timedelta(days=2))
        assert third.pages == 1 and third.newly_missing == []
        assert _history_urls(client) == [older_name, archive_name]
    else:
        # A previously flagged filing may reappear in an archive after the recent boundary has advanced.
        client.older_pages[archive_name] = synthetic_page(cik, "archive", "ROLL", "1231", [anchor, old])["filings"]["recent"]
        page["filings"]["files"][1]["filingCount"] = 2
        third = ingest(TODAY + timedelta(days=2))
        assert third.pages == 2 and not third.history_fetched and third.newly_missing == []
        assert conn.execute("SELECT not_seen_since FROM filings WHERE accession_number = %s", (old["accession"],)).fetchone()["not_seen_since"] is None


@pytest.mark.parametrize("same_start", [False, True])
def test_rollover_reads_both_archives_when_a_filing_day_crosses_the_partition_boundary(schema, same_start):
    _, conn = schema
    cik = 1
    old = {"accession": "0000000001-25-000010", "form": "10-K", "filed": date(2025, 2, 20),
           "period": date(2024, 12, 31), "xbrl": 0}
    recent = {"accession": "0000000001-25-000020", "form": "10-Q", "filed": date(2025, 5, 5),
              "period": date(2025, 3, 31), "xbrl": 0}
    page = synthetic_page(cik, "Rollover Corp", "ROLL", "1231", [recent, old])
    client = FakeEdgarClient({cik: page})
    run_id = conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]
    loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=TODAY)
    _remove_accession(page, old["accession"])
    older_name = add_history(client, cik)  # also eligible if its next boundary is the candidate day
    older_descriptor = copy.deepcopy(page["filings"]["files"][0])
    first_name, second_name = "CIK0000000001-submissions-002.json", "CIK0000000001-submissions-003.json"
    anchor_date = old["filed"] if same_start else date(2024, 11, 5)
    anchor = {"accession": "0000000001-24-000009", "form": "8-K", "filed": anchor_date,
              "period": anchor_date, "xbrl": 0}
    same_day = {**anchor, "accession": "0000000001-25-000011", "filed": old["filed"], "period": old["filed"]}
    client.older_pages[first_name] = synthetic_page(cik, "archive", "ROLL", "1231", [anchor, old])["filings"]["recent"]
    client.older_pages[second_name] = synthetic_page(cik, "archive", "ROLL", "1231", [same_day])["filings"]["recent"]
    page["filings"]["files"] = [
        {"name": second_name, "filingCount": 1, "filingFrom": str(old["filed"]), "filingTo": str(old["filed"])},
        older_descriptor,
        {"name": first_name, "filingCount": 2, "filingFrom": str(anchor_date), "filingTo": str(anchor_date)},
    ]
    second_run = conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]
    result = loader.ingest_filer(conn, client, second_run, cik, backfill=False, overrides_fye=None, today=TODAY + timedelta(days=1))
    assert result.pages == (4 if same_start else 3) and not result.history_fetched and result.newly_missing == []
    assert _history_urls(client) == ([second_name, older_name, first_name] if same_start else [second_name, first_name])
    assert conn.execute("SELECT not_seen_since FROM filings WHERE accession_number = %s", (old["accession"],)).fetchone()["not_seen_since"] is None
