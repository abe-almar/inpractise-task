"""List candidates for docs/analyst-question.md from the original twenty companies and fixed ten-year window.

Read-only: current filer categories identify possible late reports; historical categories and amendment
purposes still need source-document review. Run from the repository root with DATABASE_URL set:

    .venv/bin/python scripts/analyst_question.py
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sec_filings import db  # noqa: E402
from sec_filings.checks import NT_GRACE, filer_category, next_business_day, statutory_deadline  # noqa: E402

WINDOW_START = date(2016, 11, 7)  # ten years before the day this was written
TODAY = date(2026, 11, 7)
# Keep the candidate query tied to the original scope even after companies are added or removed.
TICKERS = "AAPL MSFT JPM XOM WMT KO GE F T TSLA PLUG GME AMC CVNA LCID SAP SHEL BABA NVO TM".split()


def main() -> None:
    conn = db.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT c.ticker, f.cik, f.form, f.is_amendment, f.fiscal_year, f.fiscal_period,
                   COALESCE(f.report_date_corrected, f.report_date) AS report_date, f.filing_date,
                   f.accession_number, f.primary_doc_description, f.primary_document_url,
                   fl.category, fl.entity_type, fl.name,
                   (SELECT count(*) > 0 FROM filings q WHERE q.cik = f.cik AND q.family = 'quarterly') AS files_10q
            FROM filings f JOIN company_ciks cc ON cc.cik = f.cik OR cc.cik = ANY(f.also_filed_by_ciks)
            JOIN companies c ON c.ticker = cc.ticker
            JOIN filers fl ON fl.cik = f.cik
            WHERE f.family = 'annual' AND f.filing_date BETWEEN %s AND %s AND c.ticker = ANY(%s)
            ORDER BY c.ticker, f.filing_date, f.accession_number
            """,
            (WINDOW_START, TODAY, TICKERS),
        )
        annual = cur.fetchall()
        cur.execute(
            """
            SELECT DISTINCT c.ticker, f.form, f.fiscal_year, f.fiscal_period,
                   COALESCE(f.report_date_corrected, f.report_date) AS report_date, f.filing_date, f.accession_number
            FROM filings f JOIN company_ciks cc ON cc.cik = f.cik OR cc.cik = ANY(f.also_filed_by_ciks)
            JOIN companies c ON c.ticker = cc.ticker
            WHERE f.family = 'late_notice' AND f.filing_date <= %s AND c.ticker = ANY(%s)
            ORDER BY c.ticker, f.filing_date, f.accession_number
            """,
            (TODAY, TICKERS),
        )
        notices = cur.fetchall()
        notice_dates = {}
        for notice in notices:
            if notice["form"].startswith("NT 10-K") or notice["form"] == "NT 20-F":
                key = (notice["ticker"], notice["report_date"])
                notice_dates[key] = min(notice_dates.get(key, notice["filing_date"]), notice["filing_date"])

    print(f"# Analyst question candidates (filings: {WINDOW_START} through {TODAY})\n")
    print("## Late-filing notices for annual reports (NT 10-K / NT 20-F) and quarterly reports (NT 10-Q)\n")
    print("| ticker | form | fiscal label | period end | filed | accession |")
    print("|---|---|---|---|---|---|")
    for n in notices:
        if n["filing_date"] < WINDOW_START:
            continue
        print(f"| {n['ticker']} | {n['form']} | {n['fiscal_year']} {n['fiscal_period']} | {n['report_date']} | {n['filing_date']} | {n['accession_number']} |")

    print("\n## Original annual reports filed after the statutory deadline (deadline from the filer's CURRENT category)\n")
    print("| ticker | form | fiscal year | period end | filed | days after period end | category | deadline | days late | NT filed |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    late_rows = []
    unsupported_deadlines = []
    for a in annual:
        if a["is_amendment"] or a["report_date"] is None:
            continue
        cat = filer_category(a["category"], a["entity_type"], a["files_10q"], a["form"])
        deadline = statutory_deadline(a["report_date"], cat, "annual")
        if deadline is None:
            unsupported_deadlines.append(f"{a['ticker']} {a['form']} FY{a['fiscal_year']}")
            continue
        nt_date = notice_dates.get((a["ticker"], a["report_date"]))
        nt = nt_date is not None
        timely_notice = nt and nt_date <= next_business_day(deadline + timedelta(days=1))
        if timely_notice:
            deadline = next_business_day(deadline + timedelta(days=NT_GRACE["annual"]))
        late = (a["filing_date"] - deadline).days
        if late > 0 or nt:
            late_rows.append(a)
            print(f"| {a['ticker']} | {a['form']} | {a['fiscal_year']} | {a['report_date']} | {a['filing_date']} | "
                  f"{(a['filing_date'] - a['report_date']).days} | {cat} | {deadline} | {max(late, 0)} | {'yes' if nt else 'no'} |")
    if not late_rows:
        print("| (none) | | | | | | | | | |")
    if unsupported_deadlines:
        print("\nDeadline not evaluated (requires the applicable local rule): " + ", ".join(unsupported_deadlines))

    print("\n## Amended annual reports (10-K/A, 20-F/A) in the window\n")
    print("| ticker | form | fiscal year | period end | amendment filed | days after original period end | EDGAR description | document |")
    print("|---|---|---|---|---|---|---|---|")
    for a in annual:
        if not a["is_amendment"]:
            continue
        print(f"| {a['ticker']} | {a['form']} | {a['fiscal_year']} | {a['report_date']} | {a['filing_date']} | "
              f"{(a['filing_date'] - a['report_date']).days if a['report_date'] else ''} | {a['primary_doc_description'] or ''} | {a['primary_document_url']} |")

    print("\n## Days from period end to filing, original annual reports, per company (min / median / max in the window)\n")
    print("| ticker | annual reports | min | median | max | category used |")
    print("|---|---|---|---|---|---|")
    by = {}
    for a in annual:
        if a["is_amendment"] or a["report_date"] is None:
            continue
        by.setdefault(a["ticker"], []).append(a)
    for t, rows in sorted(by.items()):
        d = sorted((r["filing_date"] - r["report_date"]).days for r in rows)
        cat = filer_category(rows[-1]["category"], rows[-1]["entity_type"], rows[-1]["files_10q"], rows[-1]["form"])
        print(f"| {t} | {len(d)} | {d[0]} | {d[len(d)//2]} | {d[-1]} | {cat} |")
    conn.close()


if __name__ == "__main__":
    main()
