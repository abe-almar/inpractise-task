"""Replay tests (docs/decisions.md D5 and D10) against a fresh Postgres schema with a fake EDGAR client that serves
the trimmed real pages in tests/fixtures. Runs only when TEST_DATABASE_URL is set, for example

    TEST_DATABASE_URL=postgresql://sec:sec@localhost:5432/sec_filings pytest tests/test_replay_db.py

No network: every page, and every companyfacts document, comes from the fixtures or from this file."""

from __future__ import annotations

import copy
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from sec_filings import checks, db, loader, report, verify
from sec_filings.edgar import COMPANYFACTS_URL, SUBMISSIONS_URL, TICKERS_URL, EdgarError, EdgarNotFound, Stats, document_url
from sec_filings.flow import daily_run
from sec_filings.settings import Settings

from conftest import FIXTURE_FILES, TRACKED_FORMS, all_fixtures, load_fixture

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set TEST_DATABASE_URL (e.g. postgresql://sec:sec@localhost:5432/sec_filings) to run the Postgres replay test",
)

TODAY = date(2026, 11, 7)          # the day the fixtures were fetched; fixed so the overdue check is deterministic
AAPL, XOM_NEW, XOM_OLD = 320193, 2115436, 34088
SHARED_Q2_2026_10Q = "0000034088-26-000093"   # listed on both Exxon pages (same accession, period 2026-06-30)
APPLE_1997_10QA = "0000320193-97-000021"      # EDGAR's period of report is the filing date; corrected in overrides
MIGRATIONS = ["001_init.sql", "002_co_registrants.sql", "003_review_pass_1.sql", "004_filing_corrections.sql"]
CHECK_NAMES = {
    "run_complete", "http_health", "tickers_resolved", "cik_changes_acknowledged", "fiscal_year_end_known", "history_gap",
    "no_filings_lost", "labels_complete", "labels_unique", "labels_unique_across_ciks", "labels_inferred_for_amendments",
    "labels_far_from_calendar", "overdue_reports", "labels_match_sec", "news",
}

CORRECTION_TOML = f"""
[[filing_corrections]]
accession = "{APPLE_1997_10QA}"
report_date = 1997-03-28
note = "Apple 10-Q/A filed 1997-10-07 amends the 10-Q for the quarter ended 28 March 1997 (fiscal 1997 Q2)"
evidence = "cover page of https://www.sec.gov/Archives/edgar/data/320193/0000320193-97-000021.txt, read 2026-11-07"
"""


class FakeEdgarClient:
    """Serves company_tickers.json, submissions pages and companyfacts from dicts, with the real client's interface.
    The dicts are the test's to mutate between runs; every answer is a deep copy."""

    def __init__(self, pages: dict[int, dict], older_pages: dict[str, dict] | None = None,
                 companyfacts: dict[int, dict] | None = None) -> None:
        self.pages = pages
        self.older_pages = older_pages or {}
        self.facts = companyfacts or {}
        self.tickers = {
            str(i): {"cik_str": cik, "ticker": page["tickers"][0], "title": page["name"]}
            for i, (cik, page) in enumerate(sorted(pages.items()))
            if page.get("tickers")
        }
        self.stats = Stats()

    def _serve(self, url: str, body: dict) -> tuple[dict, bytes]:
        self.stats.requests += 1
        self.stats.urls.append(url)
        raw = json.dumps(body, sort_keys=True).encode("utf-8")
        self.stats.bytes += len(raw)
        return copy.deepcopy(body), raw

    def company_tickers(self) -> tuple[dict, bytes]:
        return self._serve(TICKERS_URL, self.tickers)

    def submissions(self, cik: int) -> tuple[dict, bytes]:
        if cik not in self.pages:
            raise EdgarNotFound(f"no fixture for CIK {cik}")
        return self._serve(SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json"), self.pages[cik])

    def submissions_page(self, name: str) -> tuple[dict, bytes]:
        if name not in self.older_pages:
            raise EdgarNotFound(f"no fixture for older page {name}")
        return self._serve(SUBMISSIONS_URL.format(name=name), self.older_pages[name])

    def companyfacts(self, cik: int) -> tuple[dict, bytes]:
        if cik not in self.facts:
            raise EdgarNotFound(f"no companyfacts for CIK {cik}")
        return self._serve(COMPANYFACTS_URL.format(cik=cik), self.facts[cik])


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


XOM_OVERRIDE_TOML = f"""
[[extra_ciks]]
ticker = "XOM"
cik = {XOM_OLD}
name = "Exxon Mobil Corp"
valid_to = 2026-07-01
note = "holding company reorganisation on 2026-07-01; the ticker now maps to CIK {XOM_NEW}"
evidence = "tests/fixtures/CIK0000034088.json and CIK0002115436.json"
"""


@pytest.fixture
def settings(schema, tmp_path: Path) -> Settings:
    name, _ = schema
    return make_settings(name, tmp_path, ["AAPL", "XOM"], XOM_OVERRIDE_TOML + CORRECTION_TOML)


# ----------------------------------------------------------------------------- helpers

def _tracked(page: dict) -> list[str]:
    recent = page["filings"]["recent"]
    return [a for a, f in zip(recent["accessionNumber"], recent["form"]) if f in TRACKED_FORMS]


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


# ----------------------------------------------------------------------------- the replay

def test_replay_seven_runs(schema, settings, tmp_path: Path):
    name, conn = schema
    pages = all_fixtures()                       # pristine copies for the expectations
    client = FakeEdgarClient(copy.deepcopy(pages))   # the client's own copies, mutated between runs
    rows_per_page = {cik: _tracked(pages[cik]) for cik in (AAPL, XOM_NEW, XOM_OLD)}
    total_rows = sum(len(v) for v in rows_per_page.values())
    unique_rows = len(set().union(*rows_per_page.values()))
    assert total_rows == unique_rows + 1    # the one 10-Q both Exxon pages list
    assert SHARED_Q2_2026_10Q in rows_per_page[XOM_NEW] and SHARED_Q2_2026_10Q in rows_per_page[XOM_OLD]
    assert APPLE_1997_10QA in rows_per_page[AAPL]

    # ======================================================================= run 1: backfill into an empty schema
    r1 = daily_run(settings, mode="backfill", client=client, today=TODAY)
    assert r1.status == "ok", r1.error
    assert r1.mode == "backfill"
    assert r1.fetch_errors == {}
    assert (r1.ciks_expected, r1.ciks_fetched) == (3, 3)
    assert r1.filings_seen == total_rows
    assert r1.filings_inserted == unique_rows
    assert r1.filings_updated == 1
    assert r1.relabelled == unique_rows
    assert r1.requests == 4                     # company_tickers.json + three submissions pages; no older pages, no companyfacts
    assert r1.http_errors == 0
    assert not any("submissions-0" in u or "companyfacts" in u for u in client.stats.urls)
    assert r1.ok, _hard_failures(r1)
    assert conn.execute("SELECT count(*) AS n FROM filings").fetchone()["n"] == unique_rows
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE fiscal_year IS NULL").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM source_snapshots").fetchone()["n"] == 3
    assert _notes(conn, r1.run_id) == {"relabelled": unique_rows, "duration_sec": _notes(conn, r1.run_id)["duration_sec"], "fetch_errors": {}}
    c1 = _checks(conn, r1.run_id)
    assert c1["history_gap"]["passed"] is True and c1["history_gap"]["summary"] == "first successful run; nothing to compare with"
    assert c1["news"]["summary"] == f"{unique_rows} new filing(s) this run, of which 6 amendment(s) or late-filing notice(s)"
    assert c1["news"]["detail"]["sec_labels_refreshed"] == []   # backfill never refreshes SEC labels

    # the per-filer bookkeeping of the review pass: recent window start and the backfill marker
    filers = {r["cik"]: r for r in conn.execute("SELECT cik, recent_window_start, backfill_completed_at, fiscal_year_end_override FROM filers").fetchall()}
    assert set(filers) == {AAPL, XOM_NEW, XOM_OLD}
    for cik in filers:
        assert filers[cik]["recent_window_start"] == _min_filing_date(pages[cik])
        assert filers[cik]["backfill_completed_at"] is not None
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
    r2 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert r2.status == "ok", r2.error
    assert r2.ok, _hard_failures(r2)
    assert r2.run_id == r1.run_id + 1
    assert r2.filings_inserted == 0
    assert r2.filings_seen == r1.filings_seen == total_rows
    assert r2.filings_updated == r1.filings_inserted + r1.filings_updated == total_rows
    assert r2.relabelled == 0
    assert r2.requests == client.stats.requests == 8     # RunResult.requests mirrors the client's (cumulative) stats
    assert conn.execute("SELECT count(*) AS n FROM filings").fetchone()["n"] == unique_rows
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE first_seen_run = %s", (r2.run_id,)).fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE last_seen_run <> %s", (r2.run_id,)).fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE not_seen_since IS NOT NULL").fetchone()["n"] == 0
    # unchanged pages are not stored twice; the snapshot records the last run that saw the same content
    snaps = conn.execute("SELECT cik, page, run_id, last_seen_run FROM source_snapshots ORDER BY cik").fetchall()
    assert [(s["cik"], s["page"], s["run_id"], s["last_seen_run"]) for s in snaps] == [
        (XOM_OLD, "recent", r1.run_id, r2.run_id), (AAPL, "recent", r1.run_id, r2.run_id), (XOM_NEW, "recent", r1.run_id, r2.run_id),
    ]

    # ---- the checks of run 2 are on record
    c2 = _checks(conn, r2.run_id)
    hard = {n for n, c in c2.items() if c["severity"] == "hard"}
    assert hard == {"run_complete", "tickers_resolved", "cik_changes_acknowledged", "fiscal_year_end_known", "history_gap",
                    "no_filings_lost", "labels_complete", "labels_unique", "labels_inferred_for_amendments"}
    assert all(c2[n]["passed"] for n in hard)
    assert c2["cik_changes_acknowledged"]["passed"] is True
    assert c2["run_complete"]["summary"] == "fetched and validated 3 of 3 filer pages"
    assert c2["history_gap"]["passed"] is True and c2["history_gap"]["summary"].startswith("previous successful run ")
    assert c2["no_filings_lost"]["summary"] == "no filing has disappeared from the EDGAR feed"
    assert c2["labels_unique_across_ciks"]["passed"] is True and c2["labels_unique_across_ciks"]["severity"] == "soft"
    assert c2["labels_match_sec"]["severity"] == "info"      # no SEC labels loaded in these runs
    assert c2["labels_match_sec"]["summary"] == "no SEC labels loaded yet (run sec-filings verify-labels)"
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

    # ---- the reports view: AAPL 2009 FY is one 10-K with its amendment attached
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
    assert row["sec_fiscal_year"] is None and row["sec_fiscal_period"] is None   # no verify-labels run here
    assert row["report_date_corrected"] is None and row["note"] is None and row["not_seen_since"] is None
    assert row["primary_document_url"] == "https://www.sec.gov/Archives/edgar/data/320193/000119312509214859/d10k.htm"
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
    r3 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert r3.status == "ok" and r3.filings_inserted == 0 and r3.relabelled == 0
    assert r3.filings_seen == total_rows - 1
    assert r3.ok is False and _hard_failures(r3) == ["no_filings_lost"]
    assert conn.execute("SELECT count(*) AS n FROM filings").fetchone()["n"] == unique_rows   # never deleted
    lost = conn.execute("SELECT not_seen_since, last_seen_run, first_seen_run FROM filings WHERE accession_number = %s", (gone,)).fetchone()
    assert lost == {"not_seen_since": TODAY, "last_seen_run": r2.run_id, "first_seen_run": r1.run_id}
    c3 = _checks(conn, r3.run_id)
    assert c3["no_filings_lost"]["passed"] is False and c3["no_filings_lost"]["severity"] == "hard"
    assert c3["no_filings_lost"]["summary"].startswith("1 filing(s) no longer listed by EDGAR (1 new this run)")
    assert c3["no_filings_lost"]["detail"]["items"] == [{
        "key": gone, "ticker": "AAPL", "cik": AAPL, "form": "10-Q", "filed": str(lost_filed := conn.execute("SELECT filing_date FROM filings WHERE accession_number = %s", (gone,)).fetchone()["filing_date"]),
        "not_seen_since": str(TODAY), "new_this_run": True,
    }]
    assert isinstance(lost_filed, date)
    assert conn.execute("SELECT checks_passed FROM runs WHERE id = %s", (r3.run_id,)).fetchone()["checks_passed"] is False
    # the reports view still answers, and says the filing is no longer listed
    assert report.answer(conn, "AAPL", 2026, "Q1")[0]["not_seen_since"] == TODAY
    # the snapshot changed, so a new one is stored
    assert conn.execute("SELECT count(*) AS n FROM source_snapshots WHERE cik = %s", (AAPL,)).fetchone()["n"] == 2

    # ======================================================================= run 4: still missing the next day
    r4 = daily_run(settings, mode="daily", client=client, today=TODAY + timedelta(days=1))
    assert r4.status == "ok" and r4.ok is False and _hard_failures(r4) == ["no_filings_lost"]
    c4 = _checks(conn, r4.run_id)
    assert c4["no_filings_lost"]["summary"].startswith("1 filing(s) no longer listed by EDGAR (0 new this run)")
    assert [(i["key"], i["new_this_run"], i["not_seen_since"]) for i in c4["no_filings_lost"]["detail"]["items"]] == [(gone, False, str(TODAY))]
    assert conn.execute("SELECT not_seen_since FROM filings WHERE accession_number = %s", (gone,)).fetchone()["not_seen_since"] == TODAY
    assert conn.execute("SELECT count(*) AS n FROM source_snapshots").fetchone()["n"] == 4

    # ======================================================================= run 5: it is back, and the correction is withdrawn
    client.pages[AAPL] = copy.deepcopy(pages[AAPL])
    settings.overrides_file.write_text(XOM_OVERRIDE_TOML, encoding="utf-8")
    r5 = daily_run(settings, mode="daily", client=client, today=TODAY + timedelta(days=2))
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

    # ======================================================================= run 6: correction back; the previous run looks old
    settings.overrides_file.write_text(XOM_OVERRIDE_TOML + CORRECTION_TOML, encoding="utf-8")
    conn.execute("UPDATE runs SET started_at = %s WHERE id = %s", (datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc), r5.run_id))
    r6 = daily_run(settings, mode="daily", client=client, today=TODAY + timedelta(days=3))
    assert r6.status == "ok" and r6.relabelled == 1
    assert conn.execute("SELECT label_method, fiscal_period FROM filings WHERE accession_number = %s", (APPLE_1997_10QA,)).fetchone() == {"label_method": "corrected", "fiscal_period": "Q2"}
    assert _hard_failures(r6) == ["history_gap"]
    c6 = _checks(conn, r6.run_id)
    assert c6["history_gap"]["detail"]["items"] == [{
        "key": str(XOM_NEW), "cik": XOM_NEW, "recent_page_starts": str(_min_filing_date(pages[XOM_NEW])), "previous_ok_run": "2026-01-01",
    }]
    assert "backfill --full" in c6["history_gap"]["summary"]
    assert _min_filing_date(pages[AAPL]) < date(2026, 1, 1) < _min_filing_date(pages[XOM_NEW])   # why only the holding company
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE not_seen_since IS NOT NULL").fetchone()["n"] == 0

    # ======================================================================= run 7: one page fails validation
    client.pages[XOM_OLD]["cik"] = "0000999999"
    r7 = daily_run(settings, mode="daily", client=client, today=TODAY + timedelta(days=4))
    assert r7.status == "ok" and r7.error is None          # the run goes on without that filer
    assert r7.fetch_errors == {XOM_OLD: f"submissions page for CIK {XOM_OLD} says cik='0000999999'"}
    assert (r7.ciks_expected, r7.ciks_fetched) == (3, 2)
    assert r7.filings_seen == total_rows - len(rows_per_page[XOM_OLD])
    assert r7.relabelled == 0
    assert _hard_failures(r7) == ["run_complete"]
    c7 = _checks(conn, r7.run_id)
    assert c7["run_complete"]["summary"] == "fetched and validated 2 of 3 filer pages; 1 failed"
    assert c7["run_complete"]["detail"]["items"] == [{"key": str(XOM_OLD), "cik": XOM_OLD, "error": r7.fetch_errors[XOM_OLD]}]
    assert c7["no_filings_lost"]["passed"] is True          # a failed fetch marks nothing as missing
    assert _notes(conn, r7.run_id)["fetch_errors"] == {str(XOM_OLD): r7.fetch_errors[XOM_OLD]}
    run_row = conn.execute("SELECT status, checks_passed, ciks_fetched, ciks_expected FROM runs WHERE id = %s", (r7.run_id,)).fetchone()
    assert run_row == {"status": "ok", "checks_passed": False, "ciks_fetched": 2, "ciks_expected": 3}
    assert conn.execute("SELECT last_run_id FROM filers WHERE cik = %s", (XOM_OLD,)).fetchone()["last_run_id"] == r6.run_id
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE cik = %s AND last_seen_run = %s", (XOM_OLD, r7.run_id)).fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM filings").fetchone()["n"] == unique_rows

    # ---- stability over the seven runs
    assert [_notes(conn, r.run_id)["relabelled"] for r in (r1, r2, r3, r4, r5, r6, r7)] == [unique_rows, 0, 0, 0, 1, 1, 0]
    assert conn.execute("SELECT notes->>'relabelled' AS n FROM runs WHERE id = %s", (r7.run_id,)).fetchone()["n"] == "0"
    assert conn.execute("SELECT count(*) AS n FROM runs WHERE status = 'ok'").fetchone()["n"] == 7
    assert conn.execute("SELECT count(*) AS n FROM runs WHERE checks_passed").fetchone()["n"] == 2
    assert conn.execute("SELECT count(*) AS n FROM ticker_cik_map").fetchone()["n"] == 14
    assert conn.execute("SELECT count(*) AS n FROM checks").fetchone()["n"] == 7 * len(CHECK_NAMES)
    assert conn.execute("SELECT also_filed_by_ciks FROM filings WHERE accession_number = %s", (SHARED_Q2_2026_10Q,)).fetchone()["also_filed_by_ciks"] == [XOM_OLD]
    assert client.stats.requests == 7 * 4
    # the tables of this test live in the test schema only
    assert conn.execute("SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = %s AND table_name = 'filings'", (name,)).fetchone()["n"] == 1


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
    changed = _filing_row(acc, 1, form="10-K/A", is_amendment=True, file_number="001-00009", report_date=date(2025, 12, 27), size_bytes=11)
    assert loader.upsert_filings(conn, [changed], run2) == (0, 1)
    conn.commit()
    row = stored()
    assert (row["form"], row["is_amendment"], row["file_number"], row["report_date"], row["size_bytes"]) == ("10-K/A", True, "001-00009", date(2025, 12, 27), 11)
    assert row["cik"] == 1 and row["also_filed_by_ciks"] == [2]
    assert conn.execute("SELECT count(*) AS n FROM filings").fetchone()["n"] == 1


def test_blank_primary_document_on_a_later_page_keeps_the_document_url(schema):
    _, conn = schema
    run_id = conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]
    conn.execute("INSERT INTO filers (cik) VALUES (1)")
    acc = "0000000001-26-000001"
    loader.upsert_filings(conn, [_filing_row(acc, 1)], run_id)
    page = {"accessionNumber": [acc], "form": ["10-K"], "filingDate": ["2026-02-02"], "reportDate": ["2025-12-31"], "primaryDocument": [""]}
    loader.upsert_filings(conn, loader.parse_page(1, page), run_id)
    conn.commit()
    row = conn.execute("SELECT primary_document, primary_document_url FROM filings WHERE accession_number = %s", (acc,)).fetchone()
    assert row["primary_document"] == "a.htm"
    assert row["primary_document_url"] == document_url(1, acc, "a.htm")


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
    filer = conn.execute("SELECT recent_window_start, backfill_completed_at FROM filers WHERE cik = %s", (cik,)).fetchone()
    assert filer["recent_window_start"] == _min_filing_date(page) and filer["backfill_completed_at"] is not None
    assert conn.execute("SELECT count(*) AS n FROM source_snapshots WHERE cik = %s", (cik,)).fetchone()["n"] == 2

    # a second backfill skips the history: it was read once and nothing there changes
    second = loader.ingest_filer(conn, client, run_id, cik, backfill=True, overrides_fye=None, today=TODAY)
    assert (second.pages, second.rows, second.inserted, second.updated) == (1, recent_rows, 0, recent_rows)
    assert second.history_fetched is False
    assert client.stats.requests == 3
    # the 2012 10-K is outside the recent window, so not fetching it does not make it "missing"
    assert second.newly_missing == [] and conn.execute("SELECT count(*) AS n FROM filings WHERE not_seen_since IS NOT NULL").fetchone()["n"] == 0

    # full=True re-reads everything
    third = loader.ingest_filer(conn, client, run_id, cik, backfill=True, overrides_fye=None, today=TODAY, full=True)
    assert (third.pages, third.history_fetched, third.inserted, third.updated) == (2, True, 0, recent_rows + 1)
    assert client.stats.requests == 5
    # a daily fetch never touches the history pages
    daily = loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=TODAY, full=True)
    assert (daily.pages, daily.history_fetched) == (1, False)
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE cik = %s", (cik,)).fetchone()["n"] == recent_rows + 1

    # a page that fails validation is an EdgarError before anything is written for that filer
    client.pages[cik]["cik"] = "0000000042"
    with pytest.raises(EdgarError, match="says cik='0000000042'"):
        loader.ingest_filer(conn, client, run_id, cik, backfill=False, overrides_fye=None, today=TODAY)
    conn.rollback()


# ----------------------------------------------------------------------------- SEC labels: refresh and judgement

def _companyfacts(labels: dict[str, tuple[int, str, str]]) -> dict:
    """A minimal companyfacts document: one fact per accession carrying the SEC's fy/fp label."""
    facts = [{"accn": accn, "form": form, "fy": fy, "fp": fp, "end": "2026-01-01", "val": 1, "filed": "2026-01-02"}
             for accn, (fy, fp, form) in labels.items()]
    return {"cik": 320193, "entityName": "Apple Inc.", "facts": {"us-gaap": {"Revenues": {"units": {"USD": facts}}}}}


def test_daily_run_refreshes_sec_labels_and_judges_disagreements(schema, tmp_path: Path):
    name, conn = schema
    pages = all_fixtures()
    apple = pages[AAPL]
    fy2026 = _accession(apple, "10-K", "2026-09-26")      # filed 2026-11-03
    fy2025 = _accession(apple, "10-K", "2025-09-27")      # filed 2025-10-31
    q1_2026 = _accession(apple, "10-Q", "2025-12-27")     # filed 2026-01-xx
    sec = {fy2026: (2026, "FY", "10-K"), fy2025: (2024, "FY", "10-K"), q1_2026: (2025, "Q1", "10-Q")}
    client = FakeEdgarClient(pages, companyfacts={AAPL: _companyfacts(sec)})
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
    assert client.stats.urls[-1] == COMPANYFACTS_URL.format(cik=AAPL)       # new reports -> labels refreshed
    assert r1.requests == 3
    assert conn.execute("SELECT accession_number, fiscal_year, fiscal_period, form, source FROM sec_labels ORDER BY accession_number").fetchall() == sorted(
        [{"accession_number": a, "fiscal_year": fy, "fiscal_period": fp, "form": form, "source": "companyfacts"} for a, (fy, fp, form) in sec.items()],
        key=lambda r: r["accession_number"],
    )
    c1 = _checks(conn, r1.run_id)
    assert c1["news"]["summary"].endswith("; SEC labels refreshed for 1 filer(s)")
    assert c1["news"]["detail"]["sec_labels_refreshed"] == [AAPL] and c1["news"]["detail"]["sec_label_refresh_errors"] == {}
    match = c1["labels_match_sec"]
    assert match["severity"] == "hard" and match["passed"] is False
    assert _hard_failures(r1) == ["labels_match_sec"]
    # the 2025 10-K is one year below ours, same period, filed before `until`: a documented convention;
    # the 2026 Q1 10-Q is one year below too, but filed after `until`: a real disagreement
    assert match["detail"]["items"] == [{"key": q1_2026, "ticker": "AAPL", "form": "10-Q", "report_date": "2025-12-27", "ours": "2026 Q1", "sec": "2025 Q1"}]
    assert match["detail"]["naming_convention_counts"] == {"AAPL": 1}
    assert match["detail"]["compared"] == 3
    recent = apple["filings"]["recent"]
    xbrl_reports = [a for a, f, x in zip(recent["accessionNumber"], recent["form"], recent["isXBRL"]) if f in TRACKED_FORMS and not f.startswith("NT") and x == 1]
    assert fy2026 in xbrl_reports and len(xbrl_reports) > 3
    assert match["detail"]["pending_sec_label"] == 0                              # the 10-K filed 2026-11-03 has its label
    assert match["detail"]["older_without_sec_label"] == len(xbrl_reports) - 3    # every other XBRL report has none
    assert match["summary"] == (
        f"1 real disagreement(s) with the SEC's XBRL label on 3 filings compared; 1 are documented naming conventions (AAPL 1); "
        f"0 recent XBRL filing(s) awaiting a SEC label, {len(xbrl_reports) - 3} older without one"
    )
    # the reports view carries the SEC label next to ours
    row = report.answer(conn, "AAPL", 2025, "FY")[0]
    assert (row["fiscal_year"], row["sec_fiscal_year"], row["sec_fiscal_period"]) == (2025, 2024, "FY")
    assert report.answer(conn, "AAPL", 2009, "FY")[0]["sec_fiscal_year"] is None

    # verify-labels: the raw count includes the convention; the morning check is what tells them apart
    out = verify.verify_labels(conn, client)
    assert out == {"labels_stored": 3, "compared": 3, "raw_disagreements": 2, "filers_without_xbrl": []}
    assert verify.refresh_sec_labels(conn, client, [999]) == {999: 0}      # no companyfacts: nothing stored, reported as 0

    # a person accepts the real disagreement: the check turns into information
    settings.overrides_file.write_text(conventions + CORRECTION_TOML + f'\n[[accepted_findings]]\ncheck = "labels_match_sec"\nkey = "{q1_2026}"\nnote = "read the cover page"\n', encoding="utf-8")
    r2 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert r2.ok and r2.filings_inserted == 0
    assert client.stats.requests == 3 + 1 + 2       # run 2 refreshed nothing: no new reports
    match2 = _checks(conn, r2.run_id)["labels_match_sec"]
    assert match2["severity"] == "info" and match2["passed"] is True
    assert match2["summary"].startswith("1 real disagreement(s) with the SEC's XBRL label on 3 filings compared")
    assert match2["summary"].endswith("(all 1 accepted in overrides.toml)")
    assert match2["detail"]["accepted"][0]["key"] == q1_2026 and match2["detail"]["items"] == []

    # without the convention entry the 2025 10-K is a real disagreement too
    settings.overrides_file.write_text(CORRECTION_TOML, encoding="utf-8")
    r3 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert _hard_failures(r3) == ["labels_match_sec"]
    match3 = _checks(conn, r3.run_id)["labels_match_sec"]
    assert match3["passed"] is False and [i["key"] for i in match3["detail"]["items"]] == [fy2025, q1_2026]
    assert match3["detail"]["naming_convention_counts"] == {}


def test_sec_label_refresh_failure_is_reported_not_fatal(schema, tmp_path: Path):
    name, conn = schema

    class Failing(FakeEdgarClient):
        def companyfacts(self, cik: int):
            self.stats.requests += 1
            raise EdgarError(f"companyfacts for {cik}: HTTP 503 after retries")

    client = Failing(all_fixtures())
    settings = make_settings(name, tmp_path, ["AAPL"], CORRECTION_TOML)
    r = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert r.status == "ok" and r.ok, _hard_failures(r)
    news = _checks(conn, r.run_id)["news"]
    assert news["summary"].endswith("; SEC label refresh failed for 1 filer(s)")
    assert news["detail"]["sec_label_refresh_errors"] == {str(AAPL): f"companyfacts for {AAPL}: HTTP 503 after retries"}
    assert conn.execute("SELECT count(*) AS n FROM sec_labels").fetchone()["n"] == 0
    assert _checks(conn, r.run_id)["labels_match_sec"]["severity"] == "info"


# ----------------------------------------------------------------------------- overdue reports: NT grace, 52/53 weeks

def _seed_company(conn, ticker: str, cik: int, fye: str, category: str, filings: list[tuple[str, str, str]]) -> None:
    """filings: (form, report_date, filing_date)."""
    run_id = conn.execute("INSERT INTO runs (mode) VALUES ('daily') RETURNING id").fetchone()["id"]
    conn.execute("INSERT INTO companies (ticker) VALUES (%s) ON CONFLICT DO NOTHING", (ticker,))
    conn.execute("INSERT INTO company_ciks (ticker, cik, source, is_current) VALUES (%s, %s, 'sec_tickers', true)", (ticker, cik))
    conn.execute("INSERT INTO filers (cik, name, entity_type, category, fiscal_year_end) VALUES (%s, %s, 'operating', %s, %s)", (cik, ticker, category, fye))
    for i, (form, report_date, filing_date) in enumerate(filings):
        from sec_filings import fiscal
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
