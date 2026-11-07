"""Which fiscal year and period a report covers.

Convention (decision D3 in docs/decisions.md): the fiscal year is the calendar year in which the fiscal year ENDS.
Apple's year ended 2009-09-26 is fiscal 2009; Walmart's year ended 2026-01-31 is fiscal 2026. Quarters are counted
from the previous fiscal year end: Apple's quarter ended 2025-12-27 is fiscal 2026 Q1. The rule uses only the period of
report and the company's fiscal year end (MMDD), so it works for every filing since 1994. Companies that name their
years differently (GameStop calls the year ended 2025-02-01 "fiscal 2024") get the SEC's own label stored next to
ours by `sec-filings verify-labels`; the rule itself is never bent per company.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

ANNUAL_FORMS = frozenset({"10-K", "10-K405", "10-KT", "10-KT405", "10-KSB", "20-F", "40-F"})
QUARTERLY_FORMS = frozenset({"10-Q", "10-QT", "10-QSB"})
LATE_NOTICE_FORMS = frozenset({"NT 10-K", "NT 10-K405", "NT 10-Q", "NT 20-F"})
TRANSITION_FORMS = frozenset({"10-KT", "10-KT405", "10-QT"})

# A 52/53-week year ends up to six days either side of the nominal date; ten days keeps a margin.
TOLERANCE_DAYS = 10
# Older EDGAR metadata sometimes carries the filing date as "period of report". When the two are this close, the
# period is taken as the last nominal period end that lies at least FILING_MARGIN_DAYS before the filing date.
FILING_DATE_SLACK_DAYS = 14
FILING_MARGIN_DAYS = 14


def base_form(form: str) -> str:
    form = form.strip()
    return form[:-2] if form.endswith("/A") else form


def is_amendment(form: str) -> bool:
    return form.strip().endswith("/A")


def is_transition(form: str) -> bool:
    return base_form(form) in TRANSITION_FORMS


def family(form: str) -> str | None:
    """'annual', 'quarterly', 'late_notice' or None for forms the job does not track."""
    base = base_form(form)
    if base in ANNUAL_FORMS:
        return "annual"
    if base in QUARTERLY_FORMS:
        return "quarterly"
    if base in LATE_NOTICE_FORMS:
        return "late_notice"
    return None


def report_kind(form: str) -> str | None:
    """'annual' or 'quarterly' for reports and for the late notices that refer to them."""
    fam = family(form)
    if fam in ("annual", "quarterly"):
        return fam
    if fam == "late_notice":
        return "quarterly" if "10-Q" in base_form(form) else "annual"
    return None


def fye_date(year: int, mmdd: str) -> date:
    """The nominal fiscal year end in a calendar year; 0229 is clipped to the 28th in common years."""
    month, day = int(mmdd[:2]), int(mmdd[2:])
    day = min(day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def is_month_end(d: date) -> bool:
    return d.day == calendar.monthrange(d.year, d.month)[1]


def add_months(d: date, months: int) -> date:
    """Shift by whole months; a month-end date stays a month end (30 June + 6 months is 31 December)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    day = last_day if is_month_end(d) else min(d.day, last_day)
    return date(year, month, day)


@dataclass(frozen=True)
class Label:
    fiscal_year: int | None
    fiscal_period: str | None  # 'FY', 'Q1', 'Q2', 'Q3'; 'Q4' only when something is off
    method: str  # 'rule', 'rule_filing_date', 'rule_far', 'no_fye', 'no_report_date', 'not_a_report'
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.method in ("rule", "rule_filing_date")


LABELLED_METHODS = ("rule", "rule_filing_date", "rule_far")


def valid_mmdd(mmdd: str | None) -> bool:
    if not mmdd or len(mmdd) != 4 or not mmdd.isdigit():
        return False
    month, day = int(mmdd[:2]), int(mmdd[2:])
    return 1 <= month <= 12 and 1 <= day <= 31


def label(form: str, report_date: date | None, fye_mmdd: str | None, filing_date: date | None = None) -> Label:
    """Label a filing. `filing_date` is optional and only used when the period of report looks like the filing date."""
    lab = _label_from_period(form, report_date, fye_mmdd)
    if (
        lab.method == "rule_far" and filing_date is not None and report_date is not None and fye_mmdd
        and abs((filing_date - report_date).days) <= FILING_DATE_SLACK_DAYS
    ):
        kind = report_kind(form) or "annual"
        end = last_period_end_before(filing_date, fye_mmdd, kind)
        if end is not None:
            fixed = _label_from_period(form, end, fye_mmdd)
            return Label(
                fixed.fiscal_year, fixed.fiscal_period, "rule_filing_date",
                f"EDGAR's period of report {report_date} is the filing date; labelled from the last {kind} period end "
                f"before the filing date ({end})",
            )
    return lab


def last_period_end_before(d: date, fye_mmdd: str, kind: str, margin_days: int = FILING_MARGIN_DAYS) -> date | None:
    """The latest nominal period end of `kind` ('annual' or 'quarterly') at least `margin_days` before `d`."""
    limit = d - timedelta(days=margin_days)
    candidates: list[date] = []
    for year in (d.year - 1, d.year, d.year + 1):
        ends = nominal_period_ends(fye_mmdd, year)
        candidates.extend([ends[3]] if kind == "annual" else ends[:3])
    eligible = [c for c in candidates if c <= limit]
    return max(eligible) if eligible else None


def _label_from_period(form: str, report_date: date | None, fye_mmdd: str | None) -> Label:
    kind = report_kind(form)
    if kind is None:
        return Label(None, None, "not_a_report")
    if report_date is None:
        return Label(None, None, "no_report_date", "EDGAR gives no period of report for this filing")
    if not valid_mmdd(fye_mmdd):
        return Label(None, None, "no_fye", "no fiscal year end known for this filer; add one to overrides.toml")
    assert fye_mmdd is not None
    if kind == "annual":
        candidates = [fye_date(y, fye_mmdd) for y in (report_date.year - 1, report_date.year, report_date.year + 1)]
        nearest = min(candidates, key=lambda c: abs((c - report_date).days))
        distance = abs((nearest - report_date).days)
        if distance <= TOLERANCE_DAYS:
            return Label(nearest.year, "FY", "rule")
        return Label(
            report_date.year,
            "FY",
            "rule_far",
            f"period end {report_date} is {distance} days from the nominal year end (MMDD {fye_mmdd}); "
            "transition period or a changed fiscal year end",
        )
    # quarterly: the last nominal year end that lies clearly before the period end
    previous_end = None
    for year in (report_date.year, report_date.year - 1, report_date.year - 2):
        candidate = fye_date(year, fye_mmdd)
        if candidate <= report_date - timedelta(days=TOLERANCE_DAYS):
            previous_end = candidate
            break
    if previous_end is None:  # cannot happen with a valid MMDD, kept for safety
        return Label(None, None, "no_fye", "could not place a fiscal year end before the period")
    fiscal_year = fye_date(previous_end.year + 1, fye_mmdd).year
    months = (report_date - previous_end).days / 30.4375
    quarter = max(1, round(months / 3))
    nominal_quarter_end = add_months(previous_end, 3 * quarter)
    distance = abs((report_date - nominal_quarter_end).days)
    if quarter <= 3 and distance <= TOLERANCE_DAYS:
        return Label(fiscal_year, f"Q{quarter}", "rule")
    note = (
        f"period end {report_date} is {distance} days from the nominal quarter end {nominal_quarter_end}"
        if quarter <= 3
        else f"period end {report_date} falls in the fourth quarter, which has no 10-Q; transition period or wrong year end"
    )
    return Label(fiscal_year, f"Q{min(quarter, 4)}", "rule_far", note)


def nominal_period_ends(fye_mmdd: str, year: int) -> list[date]:
    """The four nominal period ends of the fiscal year that ends in `year`: Q1, Q2, Q3 and the year end."""
    end = fye_date(year, fye_mmdd)
    start = fye_date(year - 1, fye_mmdd)
    return [add_months(start, 3), add_months(start, 6), add_months(start, 9), end]
