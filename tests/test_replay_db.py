"""Replay test (docs/decisions.md D5 and D10): the trimmed real submissions pages loaded into a fresh Postgres schema
three times through sec_filings.flow.daily_run with a fake EDGAR client. The first run inserts everything, the second
changes nothing but the bookkeeping, the third relabels nothing. Runs only when TEST_DATABASE_URL is set, for example

    TEST_DATABASE_URL=postgresql://sec:sec@localhost:5432/sec_filings pytest tests/test_replay_db.py

No network: every page comes from tests/fixtures."""

from __future__ import annotations

import copy
import json
import os
import secrets
from datetime import date
from pathlib import Path

import pytest

from sec_filings import db, report
from sec_filings.edgar import SUBMISSIONS_URL, TICKERS_URL, EdgarNotFound, Stats
from sec_filings.flow import daily_run
from sec_filings.settings import Settings

from conftest import TRACKED_FORMS, all_fixtures

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set TEST_DATABASE_URL (e.g. postgresql://sec:sec@localhost:5432/sec_filings) to run the Postgres replay test",
)

TODAY = date(2026, 11, 7)          # the day the fixtures were fetched; fixed so the overdue check is deterministic
AAPL, XOM_NEW, XOM_OLD = 320193, 2115436, 34088
SHARED_Q2_2026_10Q = "0000034088-26-000093"   # listed on both Exxon pages (same accession, period 2026-06-30)


class FakeEdgarClient:
    """Serves company_tickers.json and the submissions pages from the fixtures, with the real client's interface."""

    def __init__(self, pages: dict[int, dict]) -> None:
        self.pages = pages
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
        raise EdgarNotFound(f"the fixtures list no older pages, yet {name} was requested")


@pytest.fixture
def schema():
    """A fresh schema with the job's tables, dropped afterwards whatever happens."""
    name = f"test_{secrets.token_hex(6)}"
    admin = db.connect(DATABASE_URL)
    admin.autocommit = True
    admin.execute(f"CREATE SCHEMA {name}")
    admin.execute(f"SET search_path TO {name}")
    try:
        applied = db.migrate(admin)
        assert applied[:2] == ["001_init.sql", "002_co_registrants.sql"]
        assert db.migrate(admin) == []          # idempotent
        yield name, admin
    finally:
        admin.execute(f"DROP SCHEMA {name} CASCADE")
        admin.close()


@pytest.fixture
def settings(schema, tmp_path: Path) -> Settings:
    name, _ = schema
    companies = tmp_path / "companies.csv"
    companies.write_text("ticker\nAAPL\nXOM\n", encoding="utf-8")
    overrides = tmp_path / "overrides.toml"
    overrides.write_text(
        f"""
[[extra_ciks]]
ticker = "XOM"
cik = {XOM_OLD}
name = "Exxon Mobil Corp"
valid_to = "2026-07-01"
note = "holding company reorganisation on 2026-07-01; the ticker now maps to CIK {XOM_NEW}"
evidence = "tests/fixtures/CIK0000034088.json and CIK0002115436.json"
""",
        encoding="utf-8",
    )
    # daily_run opens its own connection: the schema travels in the connection string
    url = DATABASE_URL + ("&" if "?" in DATABASE_URL else "?") + f"options=-c%20search_path%3D{name}"
    return Settings(
        database_url=url, user_agent="sec-filings tests tests@example.com", rate_limit_per_sec=5.0,
        companies_file=companies, overrides_file=overrides,
    )


def _tracked(page: dict) -> list[str]:
    recent = page["filings"]["recent"]
    return [a for a, f in zip(recent["accessionNumber"], recent["form"]) if f in TRACKED_FORMS]


def _notes(conn, run_id: int) -> dict:
    return conn.execute("SELECT notes FROM runs WHERE id = %s", (run_id,)).fetchone()["notes"]


def test_three_runs_are_idempotent(schema, settings):
    name, conn = schema
    pages = all_fixtures()
    client = FakeEdgarClient(pages)
    rows_per_page = {cik: _tracked(pages[cik]) for cik in (AAPL, XOM_NEW, XOM_OLD)}
    total_rows = sum(len(v) for v in rows_per_page.values())
    unique_rows = len(set().union(*rows_per_page.values()))
    assert total_rows == unique_rows + 1    # the one 10-Q both Exxon pages list
    assert SHARED_Q2_2026_10Q in rows_per_page[XOM_NEW] and SHARED_Q2_2026_10Q in rows_per_page[XOM_OLD]

    # ---- run 1: backfill into an empty schema
    r1 = daily_run(settings, mode="backfill", client=client, today=TODAY)
    assert r1.status == "ok", r1.error
    assert r1.mode == "backfill"
    assert (r1.ciks_expected, r1.ciks_fetched) == (3, 3)
    assert r1.filings_seen == total_rows
    assert r1.filings_inserted == unique_rows
    assert r1.filings_updated == 1
    assert r1.relabelled == unique_rows
    assert r1.requests == 4                     # company_tickers.json + three submissions pages, no older pages
    assert r1.http_errors == 0
    assert not any("submissions-0" in u for u in client.stats.urls)
    assert r1.ok, [c for c in r1.checks if c.severity == "hard" and not c.passed]
    assert conn.execute("SELECT count(*) AS n FROM filings").fetchone()["n"] == unique_rows
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE fiscal_year IS NULL").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM source_snapshots").fetchone()["n"] == 3
    assert _notes(conn, r1.run_id)["relabelled"] == unique_rows

    # ---- run 2: daily, nothing new
    r2 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert r2.status == "ok", r2.error
    assert r2.ok, [c for c in r2.checks if c.severity == "hard" and not c.passed]
    assert r2.run_id == r1.run_id + 1
    assert r2.filings_inserted == 0
    assert r2.filings_seen == r1.filings_seen == total_rows
    assert r2.filings_updated == r1.filings_inserted + r1.filings_updated == total_rows
    assert r2.relabelled == 0
    assert conn.execute("SELECT count(*) AS n FROM filings").fetchone()["n"] == unique_rows
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE first_seen_run = %s", (r2.run_id,)).fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE last_seen_run <> %s", (r2.run_id,)).fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE not_seen_since IS NOT NULL").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM source_snapshots").fetchone()["n"] == 3   # unchanged pages are not stored twice

    # ---- the checks of run 2 are on record
    checks = {r["name"]: r for r in report.checks_of_run(conn, r2.run_id)}
    assert len(checks) == 12
    assert checks["cik_changes_acknowledged"]["passed"] is True
    assert checks["cik_changes_acknowledged"]["severity"] == "hard"
    assert checks["run_complete"]["passed"] is True
    assert checks["tickers_resolved"]["passed"] is True
    assert checks["fiscal_year_end_known"]["passed"] is True
    assert checks["labels_complete"]["passed"] is True
    assert checks["labels_unique"]["passed"] is True
    assert checks["no_filings_lost"]["passed"] is True
    assert checks["http_health"]["passed"] is True
    assert checks["labels_match_sec"]["severity"] == "info"      # no SEC labels loaded in this test
    assert checks["news"]["summary"].startswith("0 new filing(s)")
    # Apple's 10-Q/A of 1997-10-07 carries its filing date as period of report (D3 revision 1): labelled from the
    # filing date, listed by the soft check for a person to see, but not a failure
    far = checks["labels_far_from_calendar"]
    assert far["passed"] is True and far["severity"] == "soft"
    assert [(i["key"], i["method"], i["label"]) for i in far["detail"]["items"]] == [
        ("0000320193-97-000021", "rule_filing_date", "1997 Q3"),
    ]
    assert far["summary"].startswith("1 filing(s) not labelled straight from the period of report: 1 from the filing date")
    assert conn.execute("SELECT label_method, count(*) AS n FROM filings GROUP BY 1 ORDER BY 1").fetchall() == [
        {"label_method": "rule", "n": unique_rows - 1}, {"label_method": "rule_filing_date", "n": 1},
    ]
    overdue = checks["overdue_reports"]
    assert overdue["passed"] is True
    assert [e["key"] for e in overdue["detail"]["expected_next"]] == ["AAPL/quarterly/2026-12-26", "XOM/annual/2026-12-31"]
    assert {e["filer_category"] for e in overdue["detail"]["expected_next"]} == {"large_accelerated"}
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
    assert row["primary_document_url"] == "https://www.sec.gov/Archives/edgar/data/320193/000119312509214859/d10k.htm"
    q1 = report.answer(conn, "AAPL", 2026, "Q1")
    assert [r["report_date"] for r in q1] == [date(2025, 12, 27)]
    assert report.answer(conn, "AAPL", 2026, "Q4") == []

    # ---- XOM: two CIKs, the history under the old one, the current one wins a shared filing
    ciks = {r["cik"]: r for r in conn.execute("SELECT cik, source, is_current FROM company_ciks WHERE ticker = 'XOM'").fetchall()}
    assert set(ciks) == {XOM_NEW, XOM_OLD}
    assert ciks[XOM_NEW] == {"cik": XOM_NEW, "source": "sec_tickers", "is_current": True}
    assert ciks[XOM_OLD] == {"cik": XOM_OLD, "source": "override", "is_current": False}
    fy2025 = report.answer(conn, "XOM", 2025, "FY")
    assert len(fy2025) == 1 and fy2025[0]["cik"] == XOM_OLD and fy2025[0]["form"] == "10-K"
    q3 = report.answer(conn, "XOM", 2026, "Q3")
    assert len(q3) == 1 and q3[0]["cik"] == XOM_NEW and q3[0]["accession_number"] == "0002115436-26-000020"
    # The 2026 Q2 10-Q is one combined filing (accession prefix 0000034088, the old CIK's) that both pages list
    # (D2, revised). One accession is one row: it stays with the holding company, which is ingested first as the
    # current CIK, and the old CIK is recorded in also_filed_by_ciks instead of appearing as a second original.
    q2 = report.answer(conn, "XOM", 2026, "Q2")
    assert len(q2) == 1
    assert q2[0]["accession_number"] == SHARED_Q2_2026_10Q
    assert q2[0]["cik"] == XOM_NEW
    assert q2[0]["also_filed_by_ciks"] == [XOM_OLD]
    assert q2[0]["other_originals"] is None
    assert q2[0]["form"] == "10-Q" and q2[0]["report_date"] == date(2026, 6, 30)
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE also_filed_by_ciks <> '{}'").fetchone()["n"] == 1
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE accession_number = %s", (SHARED_Q2_2026_10Q,)).fetchone()["n"] == 1
    assert conn.execute("SELECT count(*) AS n FROM filings WHERE cik = %s", (XOM_OLD,)).fetchone()["n"] == len(rows_per_page[XOM_OLD]) - 1
    summary = {r["ticker"]: r for r in report.summary(conn)}
    assert summary["XOM"]["ciks"] == f"{XOM_NEW},{XOM_OLD}"
    assert summary["XOM"]["name"] == "ExxonMobil Holdings Corp"
    assert summary["AAPL"]["annual"] == sum(1 for f in pages[AAPL]["filings"]["recent"]["form"] if f in ("10-K", "10-K405"))
    assert summary["AAPL"]["late_notices"] == 2
    assert summary["AAPL"]["odd_labels"] == 1
    assert summary["AAPL"]["first_annual"] == date(1994, 9, 30)

    # ---- run 3: relabelling is stable
    r3 = daily_run(settings, mode="daily", client=client, today=TODAY)
    assert r3.status == "ok" and r3.ok
    assert r3.filings_inserted == 0 and r3.relabelled == 0
    notes = _notes(conn, r3.run_id)
    assert notes["relabelled"] == 0
    assert conn.execute("SELECT notes->>'relabelled' AS n FROM runs WHERE id = %s", (r3.run_id,)).fetchone()["n"] == "0"
    assert conn.execute("SELECT notes->>'relabelled' AS n FROM runs WHERE id = %s", (r2.run_id,)).fetchone()["n"] == "0"
    assert conn.execute("SELECT count(*) AS n FROM runs WHERE status = 'ok' AND checks_passed").fetchone()["n"] == 3
    assert conn.execute("SELECT count(*) AS n FROM ticker_cik_map").fetchone()["n"] == 6
    assert conn.execute("SELECT count(*) AS n FROM checks").fetchone()["n"] == 36
    assert client.stats.requests == 12
    # the co-registrant list is not appended to again on later runs
    assert conn.execute("SELECT also_filed_by_ciks FROM filings WHERE accession_number = %s", (SHARED_Q2_2026_10Q,)).fetchone()["also_filed_by_ciks"] == [XOM_OLD]
    assert report.answer(conn, "XOM", 2026, "Q2")[0]["also_filed_by_ciks"] == [XOM_OLD]

    # the tables of this test live in the test schema only
    public = conn.execute(
        "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'filings'"
    ).fetchone()["n"]
    in_schema = conn.execute(
        "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = %s AND table_name = 'filings'", (name,)
    ).fetchone()["n"]
    assert in_schema == 1
    assert public in (0, 1)   # a developer database may have its own copy; this test never touched it
