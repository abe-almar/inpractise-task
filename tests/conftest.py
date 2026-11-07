"""Shared helpers: the trimmed EDGAR fixtures and the forms they keep (see tests/fixtures/make_fixtures.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# The forms make_fixtures.py keeps; the same eleven forms the loader must turn into rows.
TRACKED_FORMS = frozenset({
    "10-K", "10-K/A", "10-K405", "10-Q", "10-Q/A", "10-KT", "10-QT", "20-F", "20-F/A", "NT 10-K", "NT 10-Q",
})

FIXTURE_FILES = {
    "AAPL": "CIK0000320193.json",
    "GME": "CIK0001326380.json",
    "WMT": "CIK0000104169.json",
    "BABA": "CIK0001577552.json",
    "XOM_NEW": "CIK0002115436.json",
    "XOM_OLD": "CIK0000034088.json",
    "MSFT": "CIK0000789019.json",
    "TM": "CIK0001094517.json",
}


def load_fixture(name: str) -> dict:
    """A deep, independent copy of one fixture (callers may mutate it)."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def all_fixtures() -> dict[int, dict]:
    """Every fixture keyed by its integer CIK."""
    pages = {}
    for file_name in FIXTURE_FILES.values():
        page = load_fixture(file_name)
        pages[int(page["cik"])] = page
    return pages


@pytest.fixture(scope="session")
def fixture_pages() -> dict[int, dict]:
    return all_fixtures()
