"""sec_labels_from_companyfacts: per accession, the (fy, fp) its XBRL facts agree on; facts of untracked forms are
ignored. SEC labels are a cross-check, not the primary source."""

from __future__ import annotations

from sec_filings.verify import sec_labels_from_companyfacts


def fact(accn: str, form: str, fy, fp, **extra) -> dict:
    return {"accn": accn, "form": form, "fy": fy, "fp": fp, "end": "2024-09-28", "val": 1, "filed": "2024-11-01", **extra}


def test_most_common_label_per_accession_wins_and_untracked_forms_are_ignored():
    data = {
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [
                    fact("0000320193-24-000123", "10-K", 2024, "FY"),
                    fact("0000320193-24-000123", "10-K", 2024, "FY"),
                    fact("0000320193-24-000123", "10-K", 2023, "FY"),    # a prior-year comparative tagged oddly
                    fact("0000320193-25-000008", "10-Q", "2025", "Q1"),  # fy as a string is accepted
                    fact("0000320193-24-000050", "8-K", 2024, "Q3"),     # untracked form: ignored
                    fact("", "10-K", 2024, "FY"),                        # no accession: ignored
                    {"form": "10-K", "fy": 2024, "fp": "FY"},            # no accn key at all: ignored
                ]}},
                "Assets": {"units": {"USD": [
                    fact("0000320193-24-000123", "10-K", 2024, "FY"),
                    fact("0000320193-25-000008", "10-Q", 2025, "Q1"),
                ]}},
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {"units": {"shares": [
                    fact("0000320193-24-000123", "10-K", 2024, "FY"),
                    fact("0001326380-24-000016", "10-K/A", 2023, "FY"),
                    fact("0000000000-00-000000", "6-K", 2024, None),
                ]}},
            },
        },
    }
    labels = sec_labels_from_companyfacts(data)
    assert labels == {
        "0000320193-24-000123": (2024, "FY", "10-K"),
        "0000320193-25-000008": (2025, "Q1", "10-Q"),
        "0001326380-24-000016": (2023, "FY", "10-K/A"),
    }
    assert "0000320193-24-000050" not in labels
    assert "" not in labels


def test_missing_values_become_none():
    data = {"facts": {"us-gaap": {"X": {"units": {"USD": [
        fact("0000000001-26-000001", "10-Q", None, ""),
        fact("0000000001-26-000002", "20-F", 2026, None),
    ]}}}}}
    assert sec_labels_from_companyfacts(data) == {
        "0000000001-26-000001": (None, None, "10-Q"),
        "0000000001-26-000002": (2026, None, "20-F"),
    }


def test_empty_or_malformed_input():
    assert sec_labels_from_companyfacts({}) == {}
    assert sec_labels_from_companyfacts({"facts": None}) == {}
    assert sec_labels_from_companyfacts({"facts": {"us-gaap": {"X": {"units": None}}}}) == {}
    assert sec_labels_from_companyfacts({"facts": {"us-gaap": {"X": {}}}}) == {}
