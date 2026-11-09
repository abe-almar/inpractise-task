"""The pure helpers of the morning check (docs/decisions.md D9): filer categories, business days, accepted findings
and the printed report. The database-backed checks are exercised end to end in test_replay_db.py."""

from __future__ import annotations

from datetime import date

import pytest

from sec_filings import checks
from sec_filings.checks import (
    CheckResult, RunFacts, apply_accepted, filer_category, format_results, hard_failures, next_business_day,
    statutory_deadline,
)


def facts(**kw) -> RunFacts:
    base = dict(run_id=1, ciks_expected=3, ciks_fetched=3, http_errors=0, retries=0, duration_sec=1.0, time_budget_sec=600,
                missing_tickers=[], resolved_by_override={}, changed_tickers=[], newly_missing={}, fetch_errors={})
    base.update(kw)
    return RunFacts(**base)


@pytest.mark.parametrize(
    "category,entity_type,files_10q,expected",
    [
        ("Large accelerated filer", "operating", True, "large_accelerated"),
        ("Accelerated filer", "operating", True, "accelerated"),
        ("Non-accelerated filer\nSmaller reporting company", "operating", True, "non_accelerated"),
        ("Non-accelerated filer", "operating", True, "non_accelerated"),
        ("Smaller reporting company", "operating", True, "non_accelerated"),
        (None, "other", False, "foreign_private_issuer"),
        ("", "other", False, "foreign_private_issuer"),
        (None, None, False, "foreign_private_issuer"),
        (None, "operating", False, "non_accelerated"),   # a domestic filer with no 10-Q yet is not an FPI
        (None, "other", True, "non_accelerated"),        # whoever files 10-Qs is a domestic filer
    ],
)
def test_filer_category(category, entity_type, files_10q, expected):
    assert filer_category(category, entity_type, files_10q) == expected


@pytest.mark.parametrize("annual_form", ["20-F", "20-F/A", " 20-F "])
def test_the_annual_form_decides_the_foreign_private_issuer(annual_form):
    # Toyota and Alibaba in tests/fixtures: EDGAR calls them 'Large accelerated filer', yet the 20-F is due in four
    # months whatever the category says (D11, revised)
    assert filer_category("Large accelerated filer", "other", False, annual_form) == "foreign_private_issuer"
    assert filer_category("Accelerated filer", "operating", True, annual_form) == "foreign_private_issuer"


@pytest.mark.parametrize("annual_form", ["40-F", "40-F/A"])
def test_40f_requires_the_canadian_deadline(annual_form):
    assert filer_category("Large accelerated filer", "other", False, annual_form) == "canadian_mjds"
    assert statutory_deadline(date(2026, 12, 31), "canadian_mjds", "annual") is None


def test_without_the_annual_form_the_category_text_wins():
    assert filer_category("Large accelerated filer", "other", False) == "large_accelerated"
    assert filer_category("Large accelerated filer", "other", False, None) == "large_accelerated"
    assert filer_category("Large accelerated filer", "operating", True, "10-K") == "large_accelerated"
    assert filer_category("Non-accelerated filer", "operating", True, "10-K/A") == "non_accelerated"


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2026, 11, 7), date(2026, 11, 9)),   # Saturday -> Monday
        (date(2026, 11, 8), date(2026, 11, 9)),   # Sunday -> Monday
        (date(2026, 11, 9), date(2026, 11, 9)),   # Monday stays
        (date(2026, 11, 13), date(2026, 11, 13)), # Friday stays
    ],
)
def test_next_business_day(day, expected):
    assert next_business_day(day) == expected


def _failing(name="labels_unique", items=None, severity="hard"):
    items = [{"key": "A", "forms": ["10-K", "10-K"]}] if items is None else items
    return CheckResult(name, severity, False, "1 duplicate", {"items": items, "count": len(items)})


def test_apply_accepted_turns_a_fully_accepted_failure_into_info():
    accepted = [{"check": "labels_unique", "key": "A", "why": "documented overlap"}]
    out = apply_accepted(_failing(), accepted)
    assert out.severity == "info"
    assert out.passed is True
    assert out.name == "labels_unique"
    assert out.detail["items"] == []
    assert out.detail["accepted"] == [{"key": "A", "forms": ["10-K", "10-K"]}]
    assert out.detail["count"] == 1            # the rest of the detail is kept
    assert out.summary == "1 duplicate (all 1 accepted in overrides.toml)"   # the original summary is kept


def test_apply_accepted_keeps_failing_when_one_item_remains():
    items = [{"key": "A"}, {"key": "B"}]
    out = apply_accepted(_failing(items=items), [{"check": "labels_unique", "key": "A"}])
    assert out.passed is False
    assert out.severity == "hard"
    assert out.detail["items"] == [{"key": "B"}]
    assert out.detail["accepted"] == [{"key": "A"}]
    assert out.summary == "1 duplicate (1 accepted)"
    assert hard_failures([out]) == [out]


def test_apply_accepted_leaves_other_results_alone():
    passing = CheckResult("labels_unique", "hard", True, "fine", {"items": []})
    assert apply_accepted(passing, [{"check": "labels_unique", "key": "A"}]) is passing
    other_check = _failing()
    assert apply_accepted(other_check, [{"check": "labels_complete", "key": "A"}]) is other_check
    no_match = _failing()
    assert apply_accepted(no_match, [{"check": "labels_unique", "key": "Z"}]) is no_match
    no_items = CheckResult("run_complete", "hard", False, "x", {"ciks_expected": 3})
    assert apply_accepted(no_items, [{"check": "run_complete", "key": "3"}]) is no_items
    assert apply_accepted(_failing(), []) .passed is False


def test_apply_accepted_matches_plain_string_items_and_non_string_keys():
    result = CheckResult("tickers_resolved", "hard", False, "x", {"items": ["AAPL", "XOM"]})
    out = apply_accepted(result, [{"check": "tickers_resolved", "key": "AAPL"}])
    assert out.passed is False and out.detail["items"] == ["XOM"] and out.detail["accepted"] == ["AAPL"]
    numeric = CheckResult("no_filings_lost", "hard", False, "x", {"items": [{"key": 12}]})
    out = apply_accepted(numeric, [{"check": "no_filings_lost", "key": 12}])
    assert out.passed is True and out.severity == "info"


@pytest.mark.parametrize(
    "period_end,category,kind,expected",
    [
        (date(2027, 3, 31), "foreign_private_issuer", "annual", date(2027, 8, 2)),    # four calendar months, 31 July is a Saturday
        (date(2026, 3, 31), "foreign_private_issuer", "annual", date(2026, 7, 31)),   # a Friday stays
        (date(2026, 11, 30), "foreign_private_issuer", "annual", date(2027, 3, 31)),  # month end stays a month end
        (date(2026, 12, 31), "large_accelerated", "annual", date(2027, 3, 1)),        # 60 days, Monday
        (date(2026, 9, 26), "large_accelerated", "quarterly", date(2026, 11, 5)),     # 40 days
        (date(2026, 12, 31), "accelerated", "annual", date(2027, 3, 16)),             # 75 days
        (date(2026, 12, 31), "non_accelerated", "annual", date(2027, 3, 31)),         # 90 days
        (date(2026, 9, 30), "non_accelerated", "quarterly", date(2026, 11, 16)),      # 45 days = Saturday 14th -> Monday
        (date(2026, 9, 30), "foreign_private_issuer", "quarterly", None),            # no quarterly report expected
    ],
)
def test_statutory_deadline(period_end, category, kind, expected):
    assert statutory_deadline(period_end, category, kind) == expected


def test_statutory_deadline_tables():
    assert checks.DEADLINES["foreign_private_issuer"]["annual"] == ("months", 4)
    assert checks.DEADLINES["large_accelerated"] == {"annual": ("days", 60), "quarterly": ("days", 40)}
    assert checks.NT_GRACE == {"annual": 15, "quarterly": 5}


# check_history_gap is database-backed since review pass 2: see tests/test_replay_db.py::test_history_gap_is_persistent_state


def test_sec_labels_refreshed_check():
    ok = checks.check_sec_labels_refreshed(facts(sec_labels_refreshed=[320193, 34088]))
    assert ok.name == "sec_labels_refreshed" and ok.severity == "hard" and ok.passed is True
    assert ok.summary == "SEC labels refreshed for 2 filer(s)"
    assert ok.detail == {"items": [], "refreshed": [320193, 34088]}
    none = checks.check_sec_labels_refreshed(facts())
    assert none.passed is True and none.summary == "SEC labels refreshed for 0 filer(s)"
    bad = checks.check_sec_labels_refreshed(facts(sec_labels_refreshed=[320193], sec_label_refresh_errors={34088: "HTTP 503 after retries"}))
    assert bad.passed is False and bad.severity == "hard"
    assert bad.summary == "SEC label refresh failed for 1 filer(s); retried automatically next run"
    assert bad.detail == {"items": [{"key": "34088", "cik": 34088, "error": "HTTP 503 after retries"}], "refreshed": [320193]}
    assert hard_failures([bad]) == [bad]
    assert apply_accepted(bad, [{"check": "sec_labels_refreshed", "key": "34088"}]).passed is True


def test_sec_label_constants():
    assert checks.SEC_LABEL_PENDING_DAYS == 21
    assert not hasattr(checks, "SEC_LABEL_RETRY_DAYS")    # unlabelled XBRL reports are retried without an age bound


def test_run_complete_counts_fetch_errors():
    ok = checks.check_run_complete(facts())
    assert ok.passed is True and ok.summary == "fetched and validated 3 of 3 filer pages" and ok.detail["items"] == []
    bad = checks.check_run_complete(facts(ciks_fetched=2, fetch_errors={34088: "submissions page for CIK 34088 says cik='0000999999'"}))
    assert bad.passed is False and bad.severity == "hard"
    assert bad.summary == "fetched and validated 2 of 3 filer pages; 1 failed"
    assert bad.detail["items"] == [{"key": "34088", "cik": 34088, "error": "submissions page for CIK 34088 says cik='0000999999'"}]
    assert checks.check_run_complete(facts(ciks_expected=0, ciks_fetched=0)).passed is False
    # fetched everything but one page failed validation after being counted: still a failure
    assert checks.check_run_complete(facts(fetch_errors={1: "x"})).passed is False


def test_tickers_resolved_reports_overrides():
    out = checks.check_tickers_resolved(facts(resolved_by_override={"XOM": 34088}))
    assert out.passed is True
    assert out.summary == "every ticker is in company_tickers.json; 1 resolved by an evidenced override (XOM)"
    assert out.detail == {"items": [], "resolved_by_override": [{"ticker": "XOM", "cik": 34088}]}
    missing = checks.check_tickers_resolved(facts(missing_tickers=["ZZZZ"]))
    assert missing.passed is False and missing.detail["items"] == [{"key": "ZZZZ"}]
    assert missing.summary == "not in company_tickers.json and no current CIK override: ZZZZ"


def test_format_results_and_hard_failures():
    results = [
        CheckResult("run_complete", "hard", True, "fetched 3 of 3 filer pages", {}),
        CheckResult("labels_unique", "hard", False, "1 duplicate", {"items": [{"key": "A"}]}),
        CheckResult("overdue_reports", "soft", False, "1 overdue", {"items": [{"key": "TM/annual/2026-03-31"}]}),
        CheckResult("http_health", "soft", True, "0 EDGAR errors", {}),
        CheckResult("news", "info", True, "12 new", {"new": [{"key": "n"}], "notable": [{"key": "N"}]}),
        CheckResult("big", "hard", False, "many", {"items": [{"key": str(i)} for i in range(12)]}),
    ]
    text = format_results(results)
    lines = text.splitlines()
    assert lines[0] == "[ok  ] run_complete: fetched 3 of 3 filer pages"
    assert lines[1] == "[FAIL] labels_unique: 1 duplicate"
    assert lines[2] == "        - {'key': 'A'}"
    assert lines[3] == "[WARN] overdue_reports: 1 overdue"
    assert lines[4] == "        - {'key': 'TM/annual/2026-03-31'}"
    assert lines[5] == "[ok  ] http_health: 0 EDGAR errors"
    assert lines[6] == "[info] news: 12 new"
    assert lines[7] == "        - {'key': 'N'}"          # info lists the notable items, not every new filing
    assert lines[8] == "[FAIL] big: many"
    assert lines[9:19] == [f"        - {{'key': '{i}'}}" for i in range(10)]
    assert lines[19] == "        ... 2 more"
    assert len(lines) == 20
    assert [r.name for r in hard_failures(results)] == ["labels_unique", "big"]
    assert hard_failures([]) == []
