"""Build the trimmed EDGAR submissions fixtures used by the test suite.

Reads real submissions pages (https://data.sec.gov/submissions/CIK##########.json, saved locally) and writes a
trimmed copy per CIK into this directory:

* top-level metadata only: cik, name, entityType, category, fiscalYearEnd, tickers, exchanges, formerNames;
* filings.recent keeps only the rows whose form the job tracks (TRACKED_FORMS below) plus the first three untracked
  rows of the page (8-K, or 6-K for the foreign filers that never file an 8-K), so a parser test can prove that
  untracked forms are skipped; every parallel array stays aligned (same index = same filing);
* filings.files is empty.

Apple is the one special case: its 2009 annual report sits on the older page CIK0000320193-submissions-001.json, so
that page's tracked rows are appended to Apple's recent block (still one row per real filing, still aligned). The
test suite asks the database for "AAPL 2009 FY" and the fiscal tests use the real 2009-09-26 period end.

Usage: python tests/fixtures/make_fixtures.py SOURCE_DIR   (SOURCE_DIR holds CIK##########.json pages)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Source pages are not committed (they are 100 KB to 1 MB each). Save the files listed in SOURCES from
# https://data.sec.gov/submissions/<filename> into SOURCE_DIR, using a declared User-Agent and paced requests.
DEFAULT_SOURCE_DIR = None

TRACKED_FORMS = (
    "10-K", "10-K/A", "10-K405", "10-Q", "10-Q/A", "10-KT", "10-QT", "20-F", "20-F/A", "NT 10-K", "NT 10-Q",
)
UNTRACKED_SAMPLE = 3  # untracked rows kept per page so the parser has something to skip
METADATA_KEYS = ("cik", "name", "entityType", "category", "fiscalYearEnd", "tickers", "exchanges", "formerNames")
MAX_BYTES = 150 * 1024

# (source file, older pages whose tracked rows are merged into the recent block)
SOURCES: list[tuple[str, list[str]]] = [
    ("CIK0000320193.json", ["CIK0000320193-submissions-001.json"]),  # Apple, FYE 0926, 52/53-week
    ("CIK0001326380.json", []),                                       # GameStop, FYE 0130, 52/53-week, amendments
    ("CIK0000104169.json", []),                                       # Walmart, FYE 0131
    ("CIK0001577552.json", []),                                       # Alibaba, fiscalYearEnd null, 20-F
    ("CIK0002115436.json", []),                                       # ExxonMobil Holdings Corp (new CIK)
    ("CIK0000034088.json", []),                                       # Exxon Mobil Corp (old CIK)
    ("CIK0000789019.json", []),                                       # Microsoft, FYE 0630
    ("CIK0001094517.json", []),                                       # Toyota, FYE 0331, 20-F
]


def aligned_length(block: dict) -> int:
    lengths = {key: len(values) for key, values in block.items() if isinstance(values, list)}
    if len(set(lengths.values())) != 1:
        raise ValueError(f"parallel arrays are not aligned: {lengths}")
    return next(iter(lengths.values()))


def select_rows(block: dict, indexes: list[int]) -> dict:
    return {key: [values[i] for i in indexes] for key, values in block.items() if isinstance(values, list)}


def tracked_indexes(block: dict) -> list[int]:
    return [i for i, form in enumerate(block["form"]) if form in TRACKED_FORMS]


def untracked_sample_indexes(block: dict) -> list[int]:
    for form in ("8-K", "6-K"):
        found = [i for i, f in enumerate(block["form"]) if f == form][:UNTRACKED_SAMPLE]
        if found:
            return found
    return []


def trim(source: Path, older_pages: list[Path]) -> dict:
    data = json.loads(source.read_text(encoding="utf-8"))
    recent = data["filings"]["recent"]
    aligned_length(recent)
    keep = sorted(tracked_indexes(recent) + untracked_sample_indexes(recent))
    trimmed = select_rows(recent, keep)
    for page_path in older_pages:
        page = json.loads(page_path.read_text(encoding="utf-8"))
        aligned_length(page)
        extra = select_rows(page, tracked_indexes(page))
        if set(extra) != set(trimmed):
            raise ValueError(f"{page_path.name}: columns differ from the recent block")
        for key in trimmed:
            trimmed[key].extend(extra[key])
    aligned_length(trimmed)
    out = {key: data.get(key) for key in METADATA_KEYS}
    out["filings"] = {"recent": trimmed, "files": []}
    return out


def main(argv: list[str]) -> int:
    source_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_SOURCE_DIR
    for name, older in SOURCES:
        fixture = trim(source_dir / name, [source_dir / p for p in older])
        target = HERE / name
        text = json.dumps(fixture, indent=1, ensure_ascii=False) + "\n"
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise SystemExit(f"{target.name}: {len(text)} bytes exceeds the {MAX_BYTES} byte limit")
        target.write_text(text, encoding="utf-8")
        rows = len(fixture["filings"]["recent"]["form"])
        print(f"{target.name}: {rows} rows, {len(text.encode('utf-8'))} bytes, {fixture['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
