"""parse_page on the trimmed real submissions pages, plus the two input readers (companies CSV, overrides TOML)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from sec_filings import loader
from sec_filings.loader import Overrides, load_companies, parse_page

from conftest import FIXTURE_FILES, TRACKED_FORMS, load_fixture

UNTRACKED = {"8-K", "6-K"}


def _aligned_length(block: dict) -> int:
    lengths = {len(v) for v in block.values() if isinstance(v, list)}
    assert len(lengths) == 1, "the fixture's parallel arrays are not aligned"
    return lengths.pop()


@pytest.mark.parametrize("file_name", sorted(FIXTURE_FILES.values()))
def test_parse_page_keeps_exactly_the_tracked_forms(file_name):
    page = load_fixture(file_name)
    recent = page["filings"]["recent"]
    n = _aligned_length(recent)
    forms = recent["form"]
    expected_tracked = sum(1 for f in forms if f in TRACKED_FORMS)
    assert n - expected_tracked == 3, "each fixture carries exactly three untracked rows to skip"
    assert all(f in TRACKED_FORMS or f in UNTRACKED for f in forms)

    rows = parse_page(int(page["cik"]), recent)

    assert len(rows) == expected_tracked
    assert not any(r["form"] in UNTRACKED for r in rows)
    assert [r["accession_number"] for r in rows] == [
        recent["accessionNumber"][i] for i, f in enumerate(forms) if f in TRACKED_FORMS
    ]
    assert len({r["accession_number"] for r in rows}) == len(rows)
    for row in rows:
        assert row["cik"] == int(page["cik"])
        assert row["family"] in ("annual", "quarterly", "late_notice")
        assert isinstance(row["filing_date"], date)
        assert row["report_date"] is None or isinstance(row["report_date"], date)
        assert row["acceptance_datetime"] is None or row["acceptance_datetime"].tzinfo is not None
        assert row["primary_document_url"].startswith(f"https://www.sec.gov/Archives/edgar/data/{int(page['cik'])}/")


def test_apple_10k_fields_are_mapped():
    page = load_fixture(FIXTURE_FILES["AAPL"])
    rows = {r["accession_number"]: r for r in parse_page(320193, page["filings"]["recent"])}
    row = rows["0000320193-26-000026"]
    assert row["form"] == "10-K"
    assert row["family"] == "annual"
    assert row["is_amendment"] is False
    assert row["is_transition"] is False
    assert row["filing_date"] == date(2026, 11, 3)
    assert row["report_date"] == date(2026, 9, 26)
    assert row["acceptance_datetime"] == datetime(2026, 11, 3, 13, 5, 15, tzinfo=timezone.utc)
    assert row["acceptance_datetime"].utcoffset().total_seconds() == 0
    assert row["primary_document"] == "aapl-20260926.htm"
    assert row["primary_document_url"] == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000026/aapl-20260926.htm"
    )
    assert row["act"] == "34"
    assert row["file_number"] == "001-36743"
    assert row["film_number"] == "261514222"
    assert row["primary_doc_description"] == "10-K"
    assert row["is_xbrl"] is True
    assert row["is_inline_xbrl"] is True
    assert row["size_bytes"] == 10902198

    # the 2009 annual report (period 2009-09-26) and its amendment, from the older page merged into the fixture
    assert rows["0001193125-09-214859"]["report_date"] == date(2009, 9, 26)
    assert rows["0001193125-09-214859"]["form"] == "10-K"
    assert rows["0001193125-10-012091"]["form"] == "10-K/A"
    assert rows["0001193125-10-012091"]["is_amendment"] is True
    assert rows["0001193125-10-012091"]["family"] == "annual"

    # 1990s rows have no primary document: the URL is the filing index page
    old = rows["0000320193-94-000016"]
    assert old["primary_document"] is None
    assert old["primary_document_url"] == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019394000016/0000320193-94-000016-index.htm"
    )
    assert old["is_xbrl"] is False

    # late-filing notices form the third family
    assert rows["0001104659-06-081617"]["form"] == "NT 10-K"
    assert rows["0001104659-06-081617"]["family"] == "late_notice"
    assert rows["0001104659-06-053743"]["form"] == "NT 10-Q"
    assert rows["0001104659-06-053743"]["family"] == "late_notice"
    assert rows["0000912057-01-544436"]["form"] == "10-K405"
    assert rows["0000912057-01-544436"]["family"] == "annual"


def test_gamestop_amendments_and_toyota_20f():
    gme = {r["accession_number"]: r for r in parse_page(1326380, load_fixture(FIXTURE_FILES["GME"])["filings"]["recent"])}
    assert gme["0001326380-24-000016"]["form"] == "10-K/A"
    assert gme["0001326380-24-000016"]["is_amendment"] is True
    assert gme["0001326380-24-000016"]["family"] == "annual"
    assert gme["0001326380-24-000160"]["form"] == "10-Q/A"
    assert gme["0001326380-24-000160"]["family"] == "quarterly"
    assert gme["0001326380-24-000160"]["is_amendment"] is True

    tm = {r["accession_number"]: r for r in parse_page(1094517, load_fixture(FIXTURE_FILES["TM"])["filings"]["recent"])}
    assert tm["0001193125-26-264811"]["form"] == "20-F"
    assert tm["0001193125-26-264811"]["family"] == "annual"
    assert tm["0001193125-26-264811"]["report_date"] == date(2026, 3, 31)
    assert tm["0001193125-24-024814"]["form"] == "20-F/A"
    assert tm["0001193125-24-024814"]["is_amendment"] is True
    assert all(r["family"] == "annual" for r in tm.values())


def test_the_two_exxon_pages_share_the_q2_2026_10q():
    new = {r["accession_number"]: r for r in parse_page(2115436, load_fixture(FIXTURE_FILES["XOM_NEW"])["filings"]["recent"])}
    old = {r["accession_number"]: r for r in parse_page(34088, load_fixture(FIXTURE_FILES["XOM_OLD"])["filings"]["recent"])}
    shared = "0000034088-26-000093"
    assert shared in new and shared in old
    assert new[shared]["report_date"] == old[shared]["report_date"] == date(2026, 6, 30)
    assert new[shared]["cik"] == 2115436 and old[shared]["cik"] == 34088
    assert set(new) & set(old) == {shared}


def test_parse_page_hand_made_edge_cases():
    page = {
        "accessionNumber": ["0000000001-26-000001", "0000000001-26-000002", "0000000001-26-000003"],
        "filingDate": ["2026-02-02", "2026-02-03", "2026-02-04"],
        "reportDate": ["", "2026-01-31", "2026-01-31"],
        "acceptanceDateTime": ["", "2026-02-03T21:00:00.000Z", "not a date"],
        "form": [" 10-Q ", "8-K", "10-KT"],
        "primaryDocument": ["", "x.htm", "y.htm"],
        "isXBRL": ["", 1, 0],
        "isInlineXBRL": [0, 1, ""],
        "size": ["", 10, 20],
    }
    rows = parse_page(1, page)
    assert [r["form"] for r in rows] == ["10-Q", "10-KT"]
    first, transition = rows
    assert first["report_date"] is None
    assert first["acceptance_datetime"] is None
    assert first["primary_document"] is None
    assert first["primary_document_url"] == (
        "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/0000000001-26-000001-index.htm"
    )
    assert first["act"] is None and first["file_number"] is None and first["film_number"] is None
    assert first["is_xbrl"] is None and first["is_inline_xbrl"] is False and first["size_bytes"] is None
    assert transition["is_transition"] is True
    assert transition["family"] == "annual"
    assert transition["acceptance_datetime"] is None   # unparseable timestamps become None, not an error
    assert transition["is_xbrl"] is False and transition["is_inline_xbrl"] is None
    assert parse_page(1, {}) == []


# ----------------------------------------------------------------------------- companies.csv

def test_load_companies_dedupes_uppercases_and_ignores_blanks(tmp_path: Path):
    path = tmp_path / "companies.csv"
    path.write_text("Ticker ,name\naapl,Apple\n\n AAPL ,again\nmsft,\nxom,Exxon\n", encoding="utf-8")
    assert load_companies(path) == ["AAPL", "MSFT", "XOM"]


def test_load_companies_header_only_ticker_column(tmp_path: Path):
    path = tmp_path / "companies.csv"
    path.write_text("ticker\nGME\n", encoding="utf-8")
    assert load_companies(path) == ["GME"]


def test_load_companies_errors(tmp_path: Path):
    missing_column = tmp_path / "bad.csv"
    missing_column.write_text("symbol\nAAPL\n", encoding="utf-8")
    with pytest.raises(ValueError, match="'ticker' column"):
        load_companies(missing_column)
    empty = tmp_path / "empty.csv"
    empty.write_text("ticker\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no tickers"):
        load_companies(empty)
    no_header = tmp_path / "none.csv"
    no_header.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        load_companies(no_header)


# ----------------------------------------------------------------------------- overrides.toml

def test_overrides_load(tmp_path: Path):
    path = tmp_path / "overrides.toml"
    path.write_text(
        """
[[extra_ciks]]
ticker = "xom"
cik = "34088"
valid_to = "2026-07-01"
note = "holding company reorganisation"
evidence = "CIK0000034088.json"

[[fiscal_year_ends]]
ticker = "baba"
mmdd = "0331"
note = "20-F period ends"

[[accepted_findings]]
check = "labels_unique"
key = "LCID/1811210/annual/2020/FY"
why = "SPAC shell 10-K"
""",
        encoding="utf-8",
    )
    ov = Overrides.load(path)
    assert ov.extra_ciks == [{
        "ticker": "XOM", "cik": 34088, "valid_to": "2026-07-01",   # a quoted TOML value stays a string here
        "note": "holding company reorganisation", "evidence": "CIK0000034088.json",
    }]
    assert isinstance(ov.extra_ciks[0]["cik"], int)
    assert loader._as_date(ov.extra_ciks[0]["valid_to"]) == date(2026, 7, 1)   # what sync_companies stores
    assert ov.fiscal_year_ends == {"BABA": {"ticker": "baba", "mmdd": "0331", "note": "20-F period ends"}}
    assert ov.accepted_findings == [{"check": "labels_unique", "key": "LCID/1811210/annual/2020/FY", "why": "SPAC shell 10-K"}]


def test_overrides_bare_toml_date_is_a_date(tmp_path: Path):
    path = tmp_path / "overrides.toml"
    path.write_text('[[extra_ciks]]\nticker = "XOM"\ncik = 34088\nvalid_to = 2026-07-01\n', encoding="utf-8")
    ov = Overrides.load(path)
    assert ov.extra_ciks[0]["valid_to"] == date(2026, 7, 1)
    assert loader._as_date(ov.extra_ciks[0]["valid_to"]) == date(2026, 7, 1)


def test_overrides_missing_file_is_empty(tmp_path: Path):
    ov = Overrides.load(tmp_path / "nothing.toml")
    assert ov == Overrides()
    assert ov.extra_ciks == [] and ov.fiscal_year_ends == {} and ov.accepted_findings == []


@pytest.mark.parametrize("mmdd", ['"331"', '"1332"', '"abcd"', "331", '""'])
def test_overrides_rejects_bad_mmdd(tmp_path: Path, mmdd):
    path = tmp_path / "overrides.toml"
    path.write_text(f'[[fiscal_year_ends]]\nticker = "BABA"\nmmdd = {mmdd}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="mmdd like 0331"):
        Overrides.load(path)


def test_overrides_extra_cik_needs_ticker_and_cik(tmp_path: Path):
    path = tmp_path / "overrides.toml"
    path.write_text('[[extra_ciks]]\nticker = "XOM"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="ticker and cik"):
        Overrides.load(path)


def test_effective_fye_prefers_a_valid_override():
    assert loader.effective_fye(None, "0331") == "0331"
    assert loader.effective_fye("0926", None) == "0926"
    assert loader.effective_fye("0926", "bad") == "0926"
    assert loader.effective_fye(None, None) is None
    assert loader.effective_fye("", "") is None
