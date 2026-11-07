"""Evidence for docs/analyst-question.md: what each amended annual report in the last ten years says it is for.
Reads the first 600 KB of each amendment's primary document (where the explanatory note sits) and quotes the first
sentence that states the purpose. Read-only against EDGAR, declared user agent, one request every 0.3 s.

    .venv/bin/python docs/evidence/tools/amendment_purpose.py > docs/evidence/amendments-purpose.md
"""

from __future__ import annotations

import html
import os
import re
import sys
import time
from datetime import date

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))
from sec_filings import db  # noqa: E402

UA = os.environ.get("SEC_USER_AGENT", "sec-filings take-home abe.alcarazm@gmail.com")
WINDOW_START = date(2016, 11, 7)
PATTERNS = [
    r"(?:EXPLANATORY NOTE|Explanatory Note)\s*(.{0,700})",
    r"(?:purpose of this Amendment[^.]{0,400}\.)",
    r"(?:This Amendment No\. \d[^.]{0,400}\.)",
    r"(?:is being filed (?:solely )?(?:to|for)[^.]{0,400}\.)",
    r"(?:amends[^.]{0,300}\.)",
    r"(?:restat[^.]{0,300}\.)",
]
KEYWORDS = {
    "part_iii": r"Part III",
    "exhibit": r"[Ee]xhibit",
    "restatement": r"[Rr]estat",
    "auditor_report": r"auditor|audit report|internal control over financial reporting|ICFR|attestation",
    "xbrl": r"XBRL|Interactive Data",
    "error": r"error|correct|inadvertent",
}


def classify(text: str) -> str:
    hits = [k for k, p in KEYWORDS.items() if re.search(p, text)]
    if "restatement" in hits:
        return "restatement"
    if "part_iii" in hits:
        return "part_iii"
    if "auditor_report" in hits and "exhibit" not in hits:
        return "auditor_report"
    if "xbrl" in hits:
        return "xbrl_exhibit"
    if "exhibit" in hits:
        return "exhibit"
    if "error" in hits:
        return "correction"
    return "unclear"


def main() -> None:
    conn = db.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.ticker, f.form, f.fiscal_year, f.report_date, f.filing_date, f.accession_number, f.primary_document_url
            FROM filings f JOIN company_ciks cc ON cc.cik = f.cik JOIN companies c ON c.ticker = cc.ticker
            WHERE f.family = 'annual' AND f.is_amendment AND f.filing_date >= %s ORDER BY c.ticker, f.filing_date
            """,
            (WINDOW_START,),
        )
        rows = cur.fetchall()
    conn.close()
    session = requests.Session()
    print(f"# What the amended annual reports say they are for (filings since {WINDOW_START})\n")
    print("Quote = first sentence in the document that states the purpose; class = keyword classification, to be read, not trusted.\n")
    print("| ticker | form | fiscal year | filed | class | quote |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        time.sleep(0.3)
        resp = session.get(r["primary_document_url"], headers={"User-Agent": UA, "Range": "bytes=0-600000"}, timeout=60)
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", html.unescape(text))
        quote = ""
        for pat in PATTERNS:
            m = re.search(pat, text)
            if m:
                quote = m.group(1) if m.lastindex else m.group(0)
                break
        cls = classify(quote) if quote else "no_note_found"
        print(f"| {r['ticker']} | {r['form']} | {r['fiscal_year']} | {r['filing_date']} | {cls} | {quote[:600].replace('|', '/')} |")


if __name__ == "__main__":
    main()
