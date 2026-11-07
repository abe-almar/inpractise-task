"""The labelling rule of docs/decisions.md D3: fiscal year = calendar year in which the fiscal year ends; quarters
counted from the previous year end; ten days of tolerance for 52/53-week calendars. Every (report date, FYE) pair
below is a real one from the EDGAR pages in tests/fixtures unless the comment says otherwise."""

from __future__ import annotations

from datetime import date

import pytest

from sec_filings import fiscal
from sec_filings.fiscal import Label, label

# form, period of report, fiscal year end (MMDD), expected fiscal year, expected period, expected method
RULE_CASES = [
    # Apple, year end 26 September, 52/53-week calendar
    ("10-K", date(2009, 9, 26), "0926", 2009, "FY", "rule"),
    ("10-Q", date(2025, 12, 27), "0926", 2026, "Q1", "rule"),
    ("10-Q", date(2026, 3, 28), "0926", 2026, "Q2", "rule"),
    ("10-Q", date(2026, 6, 27), "0926", 2026, "Q3", "rule"),
    ("10-K", date(1994, 9, 30), "0926", 1994, "FY", "rule"),     # four days off the nominal date
    ("10-Q", date(2006, 7, 1), "0926", 2006, "Q3", "rule"),      # Apple's late Q3 2006, filed in December
    # Walmart, year end 31 January
    ("10-K", date(2026, 1, 31), "0131", 2026, "FY", "rule"),
    ("10-Q", date(2026, 4, 30), "0131", 2027, "Q1", "rule"),
    # Microsoft, year end 30 June
    ("10-Q", date(2025, 9, 30), "0630", 2026, "Q1", "rule"),
    ("10-K", date(2025, 6, 30), "0630", 2025, "FY", "rule"),
    # Toyota and Alibaba, 20-F, year end 31 March
    ("20-F", date(2026, 3, 31), "0331", 2026, "FY", "rule"),
    ("20-F/A", date(2023, 3, 31), "0331", 2023, "FY", "rule"),
    # GameStop, nominal year end 30 January, 52/53-week: two days off the nominal date is still 'rule'
    ("10-K", date(2025, 2, 1), "0130", 2025, "FY", "rule"),
    ("10-Q", date(2025, 5, 3), "0130", 2026, "Q1", "rule"),
    ("10-K", date(2026, 1, 31), "0130", 2026, "FY", "rule"),
    # Exxon, calendar year
    ("10-K", date(2025, 12, 31), "1231", 2025, "FY", "rule"),
    ("10-Q", date(2026, 6, 30), "1231", 2026, "Q2", "rule"),
    ("10-Q", date(2026, 9, 30), "1231", 2026, "Q3", "rule"),
    # late-filing notices carry the period they refer to (Apple, 2006)
    ("NT 10-K", date(2006, 9, 30), "0926", 2006, "FY", "rule"),
    ("NT 10-Q", date(2006, 7, 1), "0926", 2006, "Q3", "rule"),
    ("NT 10-Q", date(2025, 12, 27), "0926", 2026, "Q1", "rule"),
    # the pre-2003 annual variant
    ("10-K405", date(2001, 9, 29), "0926", 2001, "FY", "rule"),
]


@pytest.mark.parametrize("form,report_date,fye,year,period,method", RULE_CASES)
def test_label_rule(form, report_date, fye, year, period, method):
    result = label(form, report_date, fye)
    assert (result.fiscal_year, result.fiscal_period, result.method) == (year, period, method)
    assert result.ok is (method == "rule")
    assert result.note == ""


def test_amendment_keeps_the_label_of_the_original():
    result = label("10-K/A", date(2024, 12, 31), "1231")
    assert (result.fiscal_year, result.fiscal_period, result.method) == (2024, "FY", "rule")
    assert fiscal.is_amendment("10-K/A") is True
    assert fiscal.is_amendment("10-K") is False


def test_fourth_quarter_10q_is_an_anomaly():
    result = label("10-Q", date(2024, 12, 31), "1231")
    assert result.fiscal_year == 2024
    assert result.fiscal_period == "Q4"
    assert result.method == "rule_far"
    assert result.ok is False
    assert "fourth quarter" in result.note


@pytest.mark.parametrize(
    "report_date,expected_year",
    [
        (date(2024, 11, 16), 2024),   # 45 days before 2024-12-31: year of the period end
        (date(2025, 2, 14), 2025),    # 45 days after 2024-12-31: still the year of the period end, not 2024
    ],
)
def test_annual_far_from_the_nominal_year_end(report_date, expected_year):
    result = label("10-K", report_date, "1231")
    assert result.method == "rule_far"
    assert result.fiscal_period == "FY"
    assert result.fiscal_year == expected_year == report_date.year
    assert "45 days" in result.note


def test_quarterly_far_from_the_nominal_quarter_end():
    result = label("10-Q", date(2025, 11, 15), "1231")   # 46 days after the nominal Q3 end
    assert (result.fiscal_year, result.fiscal_period, result.method) == (2025, "Q3", "rule_far")
    assert "nominal quarter end 2025-09-30" in result.note


def test_tolerance_boundary():
    # exactly ten days off is still 'rule'; eleven is not
    assert label("10-K", date(2025, 1, 10), "1231").method == "rule"
    assert label("10-K", date(2025, 1, 11), "1231").method == "rule_far"


@pytest.mark.parametrize("form", ["10-K", "10-Q", "20-F", "NT 10-K", "10-K/A"])
def test_no_report_date(form):
    result = label(form, None, "1231")
    assert result == Label(None, None, "no_report_date", result.note)
    assert result.fiscal_year is None and result.fiscal_period is None
    assert result.note


@pytest.mark.parametrize("fye", [None, "", "abcd", "1332", "031", "0100"])
def test_no_fye(fye):
    result = label("10-K", date(2025, 12, 31), fye)
    assert result.method == "no_fye"
    assert result.fiscal_year is None and result.fiscal_period is None
    assert "overrides.toml" in result.note


def test_no_report_date_is_reported_before_no_fye():
    assert label("10-K", None, None).method == "no_report_date"


@pytest.mark.parametrize("form", ["8-K", "6-K", "4", "SC 13G/A", "DEF 14A", "8-K12B"])
def test_not_a_report(form):
    result = label(form, date(2025, 12, 31), "1231")
    assert result == Label(None, None, "not_a_report")
    # even without a date or a year end the answer is the same: the form decides first
    assert label(form, None, None).method == "not_a_report"


# ----------------------------------------------------------------------------- form helpers

@pytest.mark.parametrize(
    "form,expected",
    [
        ("10-K", "annual"), ("10-K/A", "annual"), ("10-K405", "annual"), ("10-KT", "annual"), ("20-F", "annual"),
        ("20-F/A", "annual"), ("40-F", "annual"), ("10-KSB", "annual"),
        ("10-Q", "quarterly"), ("10-Q/A", "quarterly"), ("10-QT", "quarterly"), ("10-QSB", "quarterly"),
        ("NT 10-K", "late_notice"), ("NT 10-Q", "late_notice"), ("NT 20-F", "late_notice"), ("NT 10-K405", "late_notice"),
        ("8-K", None), ("6-K", None), ("4", None), ("8-K12B", None), ("10-K405/A", "annual"), (" 10-Q ", "quarterly"),
    ],
)
def test_family(form, expected):
    assert fiscal.family(form) == expected


@pytest.mark.parametrize(
    "form,expected",
    [("10-K", "10-K"), ("10-K/A", "10-K"), ("20-F/A", "20-F"), ("NT 10-Q", "NT 10-Q"), (" 10-Q/A ", "10-Q"), ("8-K", "8-K")],
)
def test_base_form(form, expected):
    assert fiscal.base_form(form) == expected


@pytest.mark.parametrize("form,expected", [("10-K/A", True), ("10-Q/A", True), ("10-K", False), ("NT 10-K", False), ("20-F/A ", True)])
def test_is_amendment(form, expected):
    assert fiscal.is_amendment(form) is expected


@pytest.mark.parametrize("form,expected", [("10-KT", True), ("10-QT", True), ("10-KT/A", True), ("10-KT405", True), ("10-K", False), ("10-Q", False)])
def test_is_transition(form, expected):
    assert fiscal.is_transition(form) is expected


@pytest.mark.parametrize(
    "form,expected",
    [
        ("10-K", "annual"), ("10-Q", "quarterly"), ("20-F", "annual"), ("10-QT", "quarterly"),
        ("NT 10-K", "annual"), ("NT 10-Q", "quarterly"), ("NT 20-F", "annual"), ("NT 10-K405", "annual"),
        ("8-K", None), ("6-K", None),
    ],
)
def test_report_kind(form, expected):
    assert fiscal.report_kind(form) == expected


@pytest.mark.parametrize(
    "mmdd,expected",
    [("1231", True), ("0926", True), ("0229", True), ("0131", True), (None, False), ("", False), ("abcd", False),
     ("1332", False), ("0000", False), ("031", False), ("01311", False), ("0132", False)],
)
def test_valid_mmdd(mmdd, expected):
    assert fiscal.valid_mmdd(mmdd) is expected


# ----------------------------------------------------------------------------- calendar helpers

def test_fye_date_clips_february_29_in_common_years():
    assert fiscal.fye_date(2023, "0229") == date(2023, 2, 28)
    assert fiscal.fye_date(2024, "0229") == date(2024, 2, 29)
    assert fiscal.fye_date(2026, "0926") == date(2026, 9, 26)
    assert fiscal.fye_date(2026, "0131") == date(2026, 1, 31)


@pytest.mark.parametrize(
    "start,months,expected",
    [
        (date(2024, 1, 31), 1, date(2024, 2, 29)),    # month-end clipping, leap year
        (date(2023, 1, 31), 1, date(2023, 2, 28)),    # month-end clipping, common year
        (date(2023, 11, 30), 3, date(2024, 2, 29)),   # across a year boundary
        (date(2025, 9, 26), 3, date(2025, 12, 26)),
        (date(2025, 9, 26), 12, date(2026, 9, 26)),
        (date(2025, 10, 31), 1, date(2025, 11, 30)),
        (date(2024, 3, 31), -1, date(2024, 2, 29)),   # negative steps clip too
        (date(2024, 1, 15), -1, date(2023, 12, 15)),
        (date(2025, 6, 30), 0, date(2025, 6, 30)),
        # a month end stays a month end (D3 revision 3: 30 June + 6 months is 31 December, not 30 December)
        (date(2025, 6, 30), 6, date(2025, 12, 31)),
        (date(2025, 6, 30), 9, date(2026, 3, 31)),
        (date(2025, 2, 28), 1, date(2025, 3, 31)),    # 28 February is a month end in a common year
        (date(2024, 2, 28), 1, date(2024, 3, 28)),    # but not in a leap year
        (date(2024, 2, 29), 1, date(2024, 3, 31)),
        (date(2025, 3, 31), 1, date(2025, 4, 30)),
    ],
)
def test_add_months(start, months, expected):
    assert fiscal.add_months(start, months) == expected


@pytest.mark.parametrize(
    "d,expected",
    [(date(2025, 6, 30), True), (date(2025, 2, 28), True), (date(2024, 2, 28), False), (date(2024, 2, 29), True),
     (date(2025, 9, 26), False), (date(2025, 12, 31), True)],
)
def test_is_month_end(d, expected):
    assert fiscal.is_month_end(d) is expected


@pytest.mark.parametrize(
    "fye,year,expected",
    [
        ("0926", 2026, [date(2025, 12, 26), date(2026, 3, 26), date(2026, 6, 26), date(2026, 9, 26)]),
        ("1231", 2024, [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31)]),
        ("0131", 2026, [date(2025, 4, 30), date(2025, 7, 31), date(2025, 10, 31), date(2026, 1, 31)]),
        ("0630", 2026, [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]),
        ("0331", 2026, [date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31)]),
    ],
)
def test_nominal_period_ends(fye, year, expected):
    assert fiscal.nominal_period_ends(fye, year) == expected


# ----------------------------------------------------------------------------- the filing-date fallback (D3 revision 1)

def test_period_of_report_equal_to_the_filing_date_is_labelled_from_the_filing_date():
    # Apple's 10-Q/A filed 1997-10-07 carries 1997-10-07 as period of report (tests/fixtures/CIK0000320193.json)
    without = label("10-Q/A", date(1997, 10, 7), "0926")
    assert (without.fiscal_year, without.fiscal_period, without.method) == (1998, "Q1", "rule_far")
    with_filing = label("10-Q/A", date(1997, 10, 7), "0926", filing_date=date(1997, 10, 7))
    assert (with_filing.fiscal_year, with_filing.fiscal_period, with_filing.method) == (1997, "Q3", "rule_filing_date")
    assert with_filing.ok is True
    assert "1997-06-26" in with_filing.note and "filing date" in with_filing.note


def test_ge_10k_from_the_decision_log():
    # GE's 10-K filed 2015-02-27 with reportDate 2015-02-27: fiscal 2014, not 2015
    result = label("10-K", date(2015, 2, 27), "1231", filing_date=date(2015, 2, 27))
    assert (result.fiscal_year, result.fiscal_period, result.method) == (2014, "FY", "rule_filing_date")
    assert "2014-12-31" in result.note


def test_filing_date_fallback_only_applies_to_off_calendar_periods_near_the_filing_date():
    # on the calendar: the filing date changes nothing
    on_calendar = label("10-K", date(2014, 12, 31), "1231", filing_date=date(2015, 1, 5))
    assert (on_calendar.fiscal_year, on_calendar.method) == (2014, "rule")
    # off the calendar and filed long after the stated period: still 'rule_far'
    far = label("10-K", date(2024, 11, 16), "1231", filing_date=date(2025, 1, 30))
    assert (far.fiscal_year, far.method) == (2024, "rule_far")
    # exactly fourteen days apart still counts as "the filing date"; fifteen does not
    assert label("10-K", date(2015, 2, 27), "1231", filing_date=date(2015, 3, 13)).method == "rule_filing_date"
    assert label("10-K", date(2015, 2, 27), "1231", filing_date=date(2015, 3, 14)).method == "rule_far"
    # the fourth-quarter 10-Q anomaly is also rescued when it is really the filing date
    q4 = label("10-Q", date(2024, 12, 31), "1231", filing_date=date(2024, 12, 31))
    assert (q4.fiscal_year, q4.fiscal_period, q4.method) == (2024, "Q3", "rule_filing_date")
    # no filing date, no fallback
    assert label("10-K", date(2015, 2, 27), "1231", None).method == "rule_far"
    # ten days after the year end is still on the calendar (TOLERANCE_DAYS): plain 'rule', fiscal 2014
    assert label("10-K", date(2015, 1, 10), "1231", filing_date=date(2015, 1, 10)) == Label(2014, "FY", "rule")
    # twelve days after: off the calendar, and no period end lies 14 days before the filing date, so 'rule_far' stays
    twelve = label("10-K", date(2015, 1, 12), "1231", filing_date=date(2015, 1, 12))
    assert (twelve.fiscal_year, twelve.method) == (2015, "rule_far")


@pytest.mark.parametrize(
    "d,fye,kind,margin,expected",
    [
        (date(2015, 2, 27), "1231", "annual", 14, date(2014, 12, 31)),
        (date(2015, 1, 10), "1231", "annual", 14, None),                 # 2014-12-31 is only ten days before; nothing qualifies
        (date(2015, 1, 10), "1231", "annual", 0, date(2014, 12, 31)),
        (date(1997, 10, 7), "0926", "quarterly", 14, date(1997, 6, 26)),
        (date(2026, 1, 2), "0926", "quarterly", 14, date(2025, 6, 26)),  # 2025-12-26 is too close; 2025-09-26 is a year end, not a quarter
    ],
)
def test_last_period_end_before(d, fye, kind, margin, expected):
    assert fiscal.last_period_end_before(d, fye, kind, margin) == expected


def test_label_ok_and_labelled_methods():
    assert fiscal.LABELLED_METHODS == ("rule", "rule_filing_date", "rule_far")
    assert Label(2024, "FY", "rule").ok is True
    assert Label(2024, "FY", "rule_filing_date").ok is True
    assert Label(2024, "FY", "rule_far").ok is False
    assert Label(None, None, "no_fye").ok is False
    assert Label(None, None, "not_a_report").ok is False
