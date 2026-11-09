"""Operational regressions: completed run records and deadlines through every registrant."""

from datetime import date

import pytest

from sec_filings import checks, flow, report
from sec_filings.edgar import EdgarError
from sec_filings.flow import daily_run
from test_replay_db import (
    DATABASE_URL, TODAY, YOUNG, FakeEdgarClient, _companyfacts, _seed_company,
    make_settings, schema, synthetic_page,
)

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="set TEST_DATABASE_URL to run Postgres replay tests")


@pytest.mark.parametrize("broken", [False, True])
def test_run_lock_covers_fetching_and_releases_on_every_exit(schema, tmp_path, broken):
    name, conn = schema
    observed = []

    class LockProbe(FakeEdgarClient):
        def company_tickers(self):
            held = conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (flow.RUN_LOCK,)).fetchone()["acquired"]
            observed.append(held)
            if held:
                conn.execute("SELECT pg_advisory_unlock(%s)", (flow.RUN_LOCK,))
            if broken:
                raise EdgarError("ticker fetch failed")
            return super().company_tickers()

    client = LockProbe({7: synthetic_page(7, "Young Filer", "YNG", "0926", [YOUNG])},
                       companyfacts={7: _companyfacts({YOUNG["accession"]: (2026, "FY", "10-K")})})
    result = daily_run(make_settings(name, tmp_path, ["YNG"], ""), client=client, today=TODAY)
    assert observed == [False], "another connection must not acquire the run lock while fetching"
    assert result.status == ("failed" if broken else "ok")
    assert conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (flow.RUN_LOCK,)).fetchone()["acquired"]
    conn.execute("SELECT pg_advisory_unlock(%s)", (flow.RUN_LOCK,))


@pytest.mark.parametrize("stage", ["refresh", "checks"])
def test_unexpected_post_load_errors_close_the_run(schema, tmp_path, monkeypatch, stage):
    name, conn = schema
    facts = _companyfacts({YOUNG["accession"]: (2026, "FY", "10-K")})
    client = FakeEdgarClient({7: synthetic_page(7, "Young Filer", "YNG", "0926", [YOUNG])},
                             companyfacts={7: facts})
    settings = make_settings(name, tmp_path, ["YNG"], "")
    if stage == "refresh":
        # Valid JSON with an invalid fiscal year reaches the real companyfacts parser.
        client.facts[7] = _companyfacts({YOUNG["accession"]: ("not-a-year", "FY", "10-K")})
        expected_error = "ValueError: invalid literal"
    else:
        def fail_checks(*args, **kwargs):
            raise RuntimeError("check evaluation failed")
        monkeypatch.setattr(checks, "run_checks", fail_checks)
        expected_error = "RuntimeError: check evaluation failed"

    result = daily_run(settings, client=client, today=TODAY)
    assert not result.ok and result.status == "failed"
    row = conn.execute("SELECT status, checks_passed, finished_at, error FROM runs WHERE id = %s",
                       (result.run_id,)).fetchone()
    assert row["status"] == "failed" and row["checks_passed"] is None
    assert row["finished_at"] is not None and row["error"].startswith(expected_error)
    assert result.requests == client.stats.requests and result.requests > 0


def test_deadline_check_reaches_shared_reports_and_notices(schema):
    _, conn = schema
    _seed_company(conn, "OLD", 1, "1231", "Large accelerated filer", [
        ("10-Q", "2026-09-30", "2026-11-02"),
        ("NT 10-K", "2026-12-31", "2027-03-02"),
    ])
    _seed_company(conn, "NEW", 2, "1231", "Large accelerated filer", [])
    # A filing's first registrant keeps ownership after a reorganisation. The successor
    # can therefore have no primary rows even though its page lists a shared report.
    conn.execute("UPDATE filings SET also_filed_by_ciks = ARRAY[2]::bigint[] WHERE family = 'quarterly'")
    result = checks.check_overdue_reports(conn, date(2027, 3, 10))
    expected = {entry["ticker"]: entry for entry in result.detail["expected_next"]}
    assert expected["NEW"]["key"] == "NEW/annual/2026-12-31"
    assert expected["NEW"]["deadline"] == "2027-03-01"
    assert "NEW" in {entry["ticker"] for entry in result.detail["items"]}

    conn.execute("UPDATE filings SET also_filed_by_ciks = ARRAY[2]::bigint[] WHERE family = 'late_notice'")
    result = checks.check_overdue_reports(conn, date(2027, 3, 10))
    expected = {entry["ticker"]: entry for entry in result.detail["expected_next"]}
    assert expected["NEW"]["deadline"] == "2027-03-16"
    assert expected["NEW"]["notice_timely"] is True
    assert "NEW" not in {entry["ticker"] for entry in result.detail["items"]}

    summaries = {entry["ticker"]: entry for entry in report.summary(conn)}
    assert summaries["NEW"]["quarterly"] == 1 and summaries["NEW"]["late_notices"] == 1
    assert summaries["NEW"]["last_period"] == date(2026, 9, 30)
    # Linking the same company to both registrants must still count the shared filing once.
    conn.execute("INSERT INTO company_ciks (ticker, cik, source) VALUES ('NEW', 1, 'override')")
    merged = {entry["ticker"]: entry for entry in report.summary(conn)}["NEW"]
    assert merged["quarterly"] == 1 and merged["late_notices"] == 1


def test_deadline_check_uses_evidenced_period_corrections(schema):
    _, conn = schema
    _seed_company(conn, "FIX", 1, "1231", "Large accelerated filer", [
        ("10-Q", "2027-09-30", "2026-11-02"),
        ("NT 10-K", "2027-12-31", "2027-03-02"),
    ])
    conn.execute("UPDATE filings SET report_date_corrected = report_date - INTERVAL '1 year'")
    result = checks.check_overdue_reports(conn, date(2027, 3, 17))
    entry = result.detail["items"][0]
    assert entry["key"] == "FIX/annual/2026-12-31"
    assert entry["last_report"] == "2026-09-30"
    assert entry["notice_timely"] is True and entry["deadline"] == "2027-03-16"
    assert entry["days_overdue"] == 1
    assert report.summary(conn)[0]["last_period"] == date(2026, 9, 30)


def test_40f_deadline_needs_the_canadian_calendar(schema):
    _, conn = schema
    _seed_company(conn, "CAN", 1, "1231", "Large accelerated filer", [
        ("40-F", "2025-12-31", "2026-03-31"),
    ])
    result = checks.check_overdue_reports(conn, date(2027, 5, 1))
    assert not result.passed and result.severity == "soft"
    entry = result.detail["items"][0]
    assert entry["filer_category"] == "canadian_mjds" and entry["deadline"] is None
    assert entry["reason"] == "Form 40-F deadline needs Canadian filing calendar"
    assert "days_overdue" not in entry
