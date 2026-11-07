"""The pure helpers of the morning check (docs/decisions.md D9): filer categories, business days, accepted findings
and the printed report. The database-backed checks are exercised end to end in test_replay_db.py."""

from __future__ import annotations

from datetime import date

import pytest

from sec_filings.checks import (
    CheckResult, apply_accepted, filer_category, format_results, hard_failures, next_business_day,
)


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


@pytest.mark.parametrize("annual_form", ["20-F", "20-F/A", "40-F", " 20-F "])
def test_the_annual_form_decides_the_foreign_private_issuer(annual_form):
    # Toyota and Alibaba in tests/fixtures: EDGAR calls them 'Large accelerated filer', yet the 20-F is due in four
    # months whatever the category says (D11, revised)
    assert filer_category("Large accelerated filer", "other", False, annual_form) == "foreign_private_issuer"
    assert filer_category("Accelerated filer", "operating", True, annual_form) == "foreign_private_issuer"


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
    assert "accepted in overrides.toml" in out.summary


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
