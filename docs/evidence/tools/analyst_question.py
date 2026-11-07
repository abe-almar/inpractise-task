"""Evidence for docs/analyst-question.md: which companies filed an annual report late, or amended one, in the
last ten years, according to the table. Read-only. Run from the repository root with DATABASE_URL set:

    .venv/bin/python docs/evidence/tools/analyst_question.py > docs/evidence/analyst-question-data.md
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from sec_filings import db  # noqa: E402
from sec_filings.checks import DEADLINES, NT_GRACE, filer_category, next_business_day  # noqa: E402

WINDOW_START = date(2016, 11, 7)  # ten years before the day this was written
TODAY = date(2026, 11, 7)


def main() -> None:
    conn = db.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.ticker, f.cik, f.form, f.is_amendment, f.fiscal_year, f.fiscal_period, f.report_date, f.filing_date,
                   f.accession_number, f.primary_doc_description, f.primary_document_url,
                   fl.category, fl.entity_type, fl.name,
                   (SELECT count(*) > 0 FROM filings q WHERE q.cik = f.cik AND q.family = 'quarterly') AS files_10q,
                   (SELECT x.form FROM filings x WHERE x.cik = f.cik AND x.family = 'annual' AND NOT x.is_amendment
                      ORDER BY x.report_date DESC NULLS LAST LIMIT 1) AS annual_form
            FROM filings f JOIN company_ciks cc ON cc.cik = f.cik JOIN companies c ON c.ticker = cc.ticker
            JOIN filers fl ON fl.cik = f.cik
            WHERE f.family = 'annual' AND f.filing_date >= %s ORDER BY c.ticker, f.filing_date
            """,
            (WINDOW_START,),
        )
        annual = cur.fetchall()
        cur.execute(
            """
            SELECT c.ticker, f.form, f.fiscal_year, f.fiscal_period, f.report_date, f.filing_date, f.accession_number
            FROM filings f JOIN company_ciks cc ON cc.cik = f.cik JOIN companies c ON c.ticker = cc.ticker
            WHERE f.family = 'late_notice' AND f.filing_date >= %s ORDER BY c.ticker, f.filing_date
            """,
            (WINDOW_START,),
        )
        notices = cur.fetchall()
        cur.execute(
            """
            SELECT f.cik, f.report_date FROM filings f WHERE f.family = 'late_notice' AND f.form LIKE 'NT 10-K%' OR f.form = 'NT 20-F'
            """
        )
        notice_keys = {(r["cik"], r["report_date"]) for r in cur.fetchall()}

    print(f"# Analyst question data (window: filings since {WINDOW_START}, as of {TODAY})\n")
    print("## Late-filing notices for annual reports (NT 10-K / NT 20-F) and quarterly reports (NT 10-Q)\n")
    print("| ticker | form | fiscal label | period end | filed | accession |")
    print("|---|---|---|---|---|---|")
    for n in notices:
        print(f"| {n['ticker']} | {n['form']} | {n['fiscal_year']} {n['fiscal_period']} | {n['report_date']} | {n['filing_date']} | {n['accession_number']} |")

    print("\n## Original annual reports filed after the statutory deadline (deadline from the filer's CURRENT category)\n")
    print("| ticker | form | fiscal year | period end | filed | days after period end | category | deadline | days late | NT filed |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    late_rows = []
    for a in annual:
        if a["is_amendment"] or a["report_date"] is None:
            continue
        cat = filer_category(a["category"], a["entity_type"], a["files_10q"], a["annual_form"])
        days = DEADLINES[cat]["annual"]
        deadline = next_business_day(a["report_date"] + timedelta(days=days))
        nt = (a["cik"], a["report_date"]) in notice_keys
        if nt:
            deadline = next_business_day(deadline + timedelta(days=NT_GRACE["annual"]))
        late = (a["filing_date"] - deadline).days
        if late > 0 or nt:
            late_rows.append(a)
            print(f"| {a['ticker']} | {a['form']} | {a['fiscal_year']} | {a['report_date']} | {a['filing_date']} | "
                  f"{(a['filing_date'] - a['report_date']).days} | {cat} | {deadline} | {max(late, 0)} | {'yes' if nt else 'no'} |")
    if not late_rows:
        print("| (none) | | | | | | | | | |")

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
        cat = filer_category(rows[-1]["category"], rows[-1]["entity_type"], rows[-1]["files_10q"], rows[-1]["annual_form"])
        print(f"| {t} | {len(d)} | {d[0]} | {d[len(d)//2]} | {d[-1]} | {cat} |")
    conn.close()


if __name__ == "__main__":
    main()
