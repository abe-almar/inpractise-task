"""parse_page on the trimmed real submissions pages, plus the two input readers (companies CSV, overrides TOML)."""

from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from sec_filings import loader
from sec_filings.edgar import EdgarError
from sec_filings.loader import Overrides, load_companies, parse_page, validate_submissions
from sec_filings.settings import load_dotenv

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
        "ticker": "XOM", "cik": 34088, "current": False, "valid_from": None, "valid_to": date(2026, 7, 1),
        "note": "holding company reorganisation", "evidence": "CIK0000034088.json",
    }]
    assert isinstance(ov.extra_ciks[0]["cik"], int)
    assert ov.fiscal_year_ends == {"BABA": [{"ticker": "baba", "mmdd": "0331", "note": "20-F period ends", "valid_from": None, "valid_to": None}]}
    assert ov.accepted_findings == [{"check": "labels_unique", "key": "LCID/1811210/annual/2020/FY", "why": "SPAC shell 10-K"}]
    assert ov.naming_conventions == {} and ov.filing_notes == {} and ov.filing_corrections == {}


def test_overrides_dates_quoted_or_bare_and_current_flag(tmp_path: Path):
    path = tmp_path / "overrides.toml"
    path.write_text(
        '[[extra_ciks]]\nticker = "XOM"\ncik = 34088\nvalid_from = 1999-11-30\nvalid_to = "2026-07-01T00:00:00"\ncurrent = true\n'
        '[[extra_ciks]]\nticker = "T"\ncik = "732717"\n',
        encoding="utf-8",
    )
    ov = Overrides.load(path)
    assert ov.extra_ciks[0]["valid_from"] == date(1999, 11, 30)
    assert ov.extra_ciks[0]["valid_to"] == date(2026, 7, 1)
    assert ov.extra_ciks[0]["current"] is True
    assert ov.extra_ciks[1] == {"ticker": "T", "cik": 732717, "current": False, "valid_from": None, "valid_to": None}


def test_overrides_fiscal_year_ends_keep_every_dated_entry_per_ticker(tmp_path: Path):
    path = tmp_path / "overrides.toml"
    path.write_text(
        '[[fiscal_year_ends]]\nticker = "abc"\nmmdd = "0630"\nvalid_to = 2019-06-30\n'
        '[[fiscal_year_ends]]\nticker = "ABC"\nmmdd = "1231"\nvalid_from = "2019-07-01"\n'
        '[[fiscal_year_ends]]\nticker = "ABC"\nmmdd = "0229"\n',
        encoding="utf-8",
    )
    ov = Overrides.load(path)
    assert [(e["mmdd"], e["valid_from"], e["valid_to"]) for e in ov.fiscal_year_ends["ABC"]] == [
        ("0630", None, date(2019, 6, 30)), ("1231", date(2019, 7, 1), None), ("0229", None, None),
    ]
    assert list(ov.fiscal_year_ends) == ["ABC"]


def test_overrides_naming_conventions(tmp_path: Path):
    path = tmp_path / "overrides.toml"
    path.write_text(
        '[[naming_conventions]]\nticker = "gme"\nsec_year_offset = -1\nnote = "start year"\n'
        '[[naming_conventions]]\nticker = "WMT"\nsec_year_offset = -1\nuntil = 2014-03-21\n'
        '[[naming_conventions]]\nticker = "ODD"\nsec_year_offset = 1\nuntil = "2020-01-01"\n'
        '[[naming_conventions]]\nticker = "DEF"\n',
        encoding="utf-8",
    )
    ov = Overrides.load(path)
    assert ov.naming_conventions["GME"] == {"ticker": "gme", "sec_year_offset": -1, "note": "start year", "until": None}
    assert ov.naming_conventions["WMT"]["until"] == date(2014, 3, 21)
    assert ov.naming_conventions["ODD"] == {"ticker": "ODD", "sec_year_offset": 1, "until": date(2020, 1, 1)}
    assert ov.naming_conventions["DEF"]["sec_year_offset"] == -1    # the default direction: year named by its start
    assert set(ov.naming_conventions) == {"GME", "WMT", "ODD", "DEF"}


@pytest.mark.parametrize("offset", ["0", "2", "-2", '"-1"'])
def test_overrides_naming_conventions_offset_must_be_one_year(tmp_path: Path, offset):
    path = tmp_path / "overrides.toml"
    path.write_text(f'[[naming_conventions]]\nticker = "GME"\nsec_year_offset = {offset}\n', encoding="utf-8")
    if offset == '"-1"':
        assert Overrides.load(path).naming_conventions["GME"]["sec_year_offset"] == -1   # a quoted number is accepted
    else:
        with pytest.raises(ValueError, match="must be -1 or 1"):
            Overrides.load(path)


def test_overrides_naming_conventions_need_a_ticker(tmp_path: Path):
    path = tmp_path / "overrides.toml"
    path.write_text('[[naming_conventions]]\nsec_year_offset = -1\n', encoding="utf-8")
    with pytest.raises(ValueError, match="needs a ticker"):
        Overrides.load(path)


def test_overrides_filing_notes(tmp_path: Path):
    path = tmp_path / "overrides.toml"
    path.write_text('[[filing_notes]]\naccession = "0001104659-21-036537"\nnote = "SPAC shell"\n', encoding="utf-8")
    assert Overrides.load(path).filing_notes == {"0001104659-21-036537": "SPAC shell"}
    path.write_text('[[filing_notes]]\naccession = "0001104659-21-036537"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="accession and note"):
        Overrides.load(path)


def test_overrides_filing_corrections(tmp_path: Path):
    path = tmp_path / "overrides.toml"
    path.write_text(
        '[[filing_corrections]]\naccession = "0000320193-97-000021"\nreport_date = 1997-03-28\nnote = "Item 4 amendment"\n'
        'evidence = "cover page read 2026-11-07"\n'
        '[[filing_corrections]]\naccession = "0000000001-26-000001"\nreport_date = "2025-12-31"\nevidence = "x"\n',
        encoding="utf-8",
    )
    ov = Overrides.load(path)
    assert ov.filing_corrections["0000320193-97-000021"] == {
        "accession": "0000320193-97-000021", "report_date": date(1997, 3, 28), "note": "Item 4 amendment",
        "evidence": "cover page read 2026-11-07",
    }
    assert ov.filing_corrections["0000000001-26-000001"]["report_date"] == date(2025, 12, 31)


@pytest.mark.parametrize(
    "body,message",
    [
        ('accession = "0000320193-97-000021"\nevidence = "x"\n', "accession and report_date"),
        ('accession = "0000320193-97-000021"\nreport_date = ""\nevidence = "x"\n', "accession and report_date"),
        ('report_date = 1997-03-28\nevidence = "x"\n', "accession and report_date"),
        ('accession = "0000320193-97-000021"\nreport_date = 1997-03-28\n', "needs evidence"),
        ('accession = "0000320193-97-000021"\nreport_date = 1997-03-28\nevidence = ""\n', "needs evidence"),
    ],
)
def test_overrides_filing_corrections_need_a_date_and_evidence(tmp_path: Path, body, message):
    path = tmp_path / "overrides.toml"
    path.write_text("[[filing_corrections]]\n" + body, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        Overrides.load(path)


def test_overrides_bare_toml_date_is_a_date(tmp_path: Path):
    path = tmp_path / "overrides.toml"
    path.write_text('[[extra_ciks]]\nticker = "XOM"\ncik = 34088\nvalid_to = 2026-07-01\n', encoding="utf-8")
    ov = Overrides.load(path)
    assert ov.extra_ciks[0]["valid_to"] == date(2026, 7, 1)


def test_as_date_accepts_dates_datetimes_and_strings():
    assert loader._as_date(None) is None and loader._as_date("") is None
    assert loader._as_date(date(2026, 7, 1)) == date(2026, 7, 1)
    assert loader._as_date(datetime(2026, 7, 1, 12, 30)) == date(2026, 7, 1)
    assert loader._as_date("2026-07-01") == date(2026, 7, 1)
    assert loader._as_date("2026-07-01T05:00:00.000Z") == date(2026, 7, 1)


def test_overrides_missing_file_is_empty(tmp_path: Path):
    ov = Overrides.load(tmp_path / "nothing.toml")
    assert ov == Overrides()
    assert ov.extra_ciks == [] and ov.fiscal_year_ends == {} and ov.accepted_findings == []
    assert ov.naming_conventions == {} and ov.filing_notes == {} and ov.filing_corrections == {}


@pytest.mark.parametrize("mmdd", ['"331"', '"1332"', '"abcd"', "331", '""', '"0231"', '"0431"'])
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


def test_effective_fye_without_entries_is_edgar_when_valid():
    assert loader.effective_fye("0926", None) == "0926"
    assert loader.effective_fye("0926", []) == "0926"
    assert loader.effective_fye("0926", [], date(2026, 9, 26)) == "0926"
    assert loader.effective_fye(None, None) is None
    assert loader.effective_fye("", []) is None
    assert loader.effective_fye("0231", []) is None      # not a real date
    assert loader.effective_fye("bad", None) is None


def test_effective_fye_undated_entry_beats_edgar_and_the_last_one_wins():
    entries = [{"mmdd": "0331"}, {"mmdd": "0630"}]
    assert loader.effective_fye(None, entries) == "0630"
    assert loader.effective_fye("1231", entries) == "0630"
    assert loader.effective_fye("1231", entries, date(2001, 1, 1)) == "0630"
    assert loader.effective_fye("1231", [{"mmdd": "0331", "valid_from": None, "valid_to": None}]) == "0331"


def test_effective_fye_dated_entry_applies_to_its_period_only():
    entries = [
        {"mmdd": "0630", "valid_from": None, "valid_to": date(2019, 6, 30)},
        {"mmdd": "1231", "valid_from": date(2019, 7, 1), "valid_to": None},
    ]
    assert loader.effective_fye("0926", entries, date(2018, 6, 30)) == "0630"
    assert loader.effective_fye("0926", entries, date(2019, 6, 30)) == "0630"    # inclusive bounds
    assert loader.effective_fye("0926", entries, date(2019, 7, 1)) == "1231"
    assert loader.effective_fye("0926", entries, date(2026, 12, 31)) == "1231"
    # without a period the dated entries are ignored: EDGAR's value is all that is left
    assert loader.effective_fye("0926", entries) == "0926"
    assert loader.effective_fye(None, entries) is None
    # a period outside every validity window falls back to the undated entry, then to EDGAR
    bounded = [{"mmdd": "0630", "valid_from": date(2010, 1, 1), "valid_to": date(2019, 6, 30)}]
    assert loader.effective_fye("0926", bounded, date(2026, 1, 1)) == "0926"
    assert loader.effective_fye("0926", bounded + [{"mmdd": "0331"}], date(2026, 1, 1)) == "0331"
    assert loader.effective_fye("0926", bounded + [{"mmdd": "0331"}], date(2015, 1, 1)) == "0630"   # dated wins inside its window


# ----------------------------------------------------------------------------- validate_submissions

def _page(cik="0000320193", n=2, **overrides):
    recent = {
        "accessionNumber": [f"0000320193-26-00000{i}" for i in range(n)],
        "form": ["10-K"] * n,
        "filingDate": ["2026-11-03"] * n,
        "reportDate": ["2026-09-26"] * n,
    }
    recent.update(overrides)
    return {"cik": cik, "name": "Apple Inc.", "filings": {"recent": recent, "files": []}}


def test_validate_submissions_returns_the_recent_block():
    page = _page()
    assert validate_submissions(320193, page) is page["filings"]["recent"]
    assert validate_submissions(320193, _page(cik=320193)) is not None       # an integer cik is fine too
    assert validate_submissions(320193, _page(cik="320193")) is not None
    assert validate_submissions(0, _page(cik="0000000000", n=1)) is not None


def test_validate_submissions_rejects_another_filers_page():
    with pytest.raises(EdgarError, match="says cik='0000034088'"):
        validate_submissions(320193, _page(cik="0000034088"))
    with pytest.raises(EdgarError, match="says cik="):
        validate_submissions(320193, {"name": "no cik at all", "filings": {"recent": {"accessionNumber": ["x"]}}})


def test_validate_submissions_rejects_an_empty_or_ragged_recent_block():
    with pytest.raises(EdgarError, match="empty recent block"):
        validate_submissions(320193, _page(n=0))
    with pytest.raises(EdgarError, match="empty recent block"):
        validate_submissions(320193, {"cik": "0000320193", "filings": {}})
    with pytest.raises(EdgarError, match="empty recent block"):
        validate_submissions(320193, {"cik": "0000320193"})
    with pytest.raises(EdgarError, match="column form has 1 entries, accessionNumber has 2"):
        validate_submissions(320193, _page(form=["10-K"]))
    with pytest.raises(EdgarError, match="column filingDate has 3 entries, accessionNumber has 2"):
        validate_submissions(320193, _page(filingDate=["2026-11-03", "2026-11-03", "2026-11-03"]))
    with pytest.raises(EdgarError, match="column form is missing"):
        validate_submissions(320193, _page(form=None))
    with pytest.raises(EdgarError, match="column form has 0 entries"):
        validate_submissions(320193, _page(form=[]))
    with pytest.raises(EdgarError, match="column filingDate is missing"):
        validate_submissions(320193, _page(filingDate=None))


def test_validate_history_page_accepts_exactly_what_was_advertised():
    loader.validate_history_page(320193, {"name": "p.json", "filingCount": 2}, _page(n=2)["filings"]["recent"])


@pytest.mark.parametrize("body, advertised, message", [
    ({}, 2, "holds 0 filings, the recent page advertised 2"),
    (_page(n=1)["filings"]["recent"], 2, "holds 1 filings, the recent page advertised 2"),
    (_page(n=2)["filings"]["recent"], None, "holds 2 filings, the recent page advertised None"),
    ({}, 0, "column form is missing"),
    (_page(form=["10-K"])["filings"]["recent"], 2, "column form has 1 entries, accessionNumber has 2"),
], ids=["empty", "truncated", "no-count-advertised", "advertised-empty", "ragged"])
def test_validate_history_page_rejects_anything_else(body, advertised, message):
    with pytest.raises(EdgarError, match=message):
        loader.validate_history_page(320193, {"name": "p.json", "filingCount": advertised}, body)


def test_dotenv_reader_takes_literal_values_and_the_export_form(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(os, "environ", {"ALREADY": "from the environment"})
    env = tmp_path / ".env"
    env.write_text('SPACED = postgresql://h/d?x=1&y=2\nexport EXPORTED="postgresql://h/e"\n# COMMENTED=no\nALREADY=from the file\n')
    load_dotenv(env)
    assert os.environ == {"SPACED": "postgresql://h/d?x=1&y=2", "EXPORTED": "postgresql://h/e", "ALREADY": "from the environment"}


def test_validate_submissions_checks_every_page_column():
    assert loader.PAGE_COLUMNS == (
        "accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "act", "fileNumber", "filmNumber",
        "primaryDocument", "primaryDocDescription", "isXBRL", "isInlineXBRL", "size", "form",
    )
    with pytest.raises(EdgarError, match="column reportDate has 1 entries, accessionNumber has 2"):
        validate_submissions(320193, _page(reportDate=["2026-09-26"]))
    for key in ("acceptanceDateTime", "act", "fileNumber", "filmNumber", "primaryDocument", "primaryDocDescription", "isXBRL", "isInlineXBRL", "size"):
        with pytest.raises(EdgarError, match=f"column {key} has 3 entries, accessionNumber has 2"):
            validate_submissions(320193, _page(**{key: ["", "", ""]}))
    # optional columns may be absent altogether; columns the loader never reads are not checked
    assert validate_submissions(320193, _page(reportDate=None)) is not None
    assert validate_submissions(320193, _page(items=["2.02"], core_type=[]))["items"] == ["2.02"]


@pytest.mark.parametrize("file_name", sorted(FIXTURE_FILES.values()))
def test_fixtures_pass_validation(file_name):
    page = load_fixture(file_name)
    assert validate_submissions(int(page["cik"]), page) is page["filings"]["recent"]


def test_fingerprint_inputs_hashes_the_bytes_and_tolerates_a_missing_overrides_file(tmp_path: Path):
    companies = tmp_path / "companies.csv"
    companies.write_bytes(b"ticker\nAAPL\n")
    first = loader.fingerprint_inputs(companies, tmp_path / "overrides.toml")
    assert first == {"companies_csv": {"path": "companies.csv", "sha256": hashlib.sha256(b"ticker\nAAPL\n").hexdigest()},
                     "overrides_toml": {"path": "overrides.toml", "sha256": None}}
    (tmp_path / "overrides.toml").write_bytes(b"# nothing yet\n")
    second = loader.fingerprint_inputs(companies, tmp_path / "overrides.toml")
    assert second["companies_csv"] == first["companies_csv"]
    assert second["overrides_toml"] == {"path": "overrides.toml", "sha256": hashlib.sha256(b"# nothing yet\n").hexdigest()}
