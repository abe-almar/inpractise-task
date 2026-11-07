"""The morning check: what tells us a run is wrong, beyond "it didn't crash".

Every check returns a CheckResult and is stored in the `checks` table for the run. Severity:
  hard  - the run is wrong or incomplete, or something needs a person's decision; the process exits non-zero.
  soft  - something a person should look at this week; printed as a warning.
  info  - news worth reading (new amendments, late-filing notices); never fails.
Findings a person has looked at and accepted are listed in overrides.toml under [[accepted_findings]] with the check
name and the key the check printed; they are then reported as info instead of failing. Nothing is auto-accepted.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

import psycopg
from psycopg.types.json import Jsonb

from sec_filings import fiscal

log = logging.getLogger(__name__)

# Statutory deadlines after the period end (Forms 10-K and 10-Q general instructions; Form 20-F general instruction
# A: four calendar months). The grace after a timely NT filing is 15 days for annual and 5 for quarterly reports.
DEADLINES: dict[str, dict[str, tuple[str, int] | None]] = {
    "large_accelerated": {"annual": ("days", 60), "quarterly": ("days", 40)},
    "accelerated": {"annual": ("days", 75), "quarterly": ("days", 40)},
    "non_accelerated": {"annual": ("days", 90), "quarterly": ("days", 45)},
    "foreign_private_issuer": {"annual": ("months", 4), "quarterly": None},
}
NT_GRACE = {"annual": 15, "quarterly": 5}
PERIOD_MATCH_DAYS = 10          # a notice's period and a nominal period end are the same period within this
SEC_LABEL_PENDING_DAYS = 21     # XBRL facts can lag the filing; younger unlabelled filings are "pending", not missing


@dataclass
class CheckResult:
    name: str
    severity: str
    passed: bool
    summary: str
    detail: dict = field(default_factory=dict)


@dataclass
class RunFacts:
    """What the run itself knows and the database does not yet."""
    run_id: int
    ciks_expected: int
    ciks_fetched: int
    http_errors: int
    retries: int
    duration_sec: float
    time_budget_sec: int
    missing_tickers: list[str]
    resolved_by_override: dict[str, int]
    changed_tickers: list[tuple[str, int, int]]
    newly_missing: dict[int, list[str]]
    fetch_errors: dict[int, str]
    previous_ok_run_started: date | None
    window_starts: dict[int, date | None]
    sec_labels_refreshed: list[int] = field(default_factory=list)
    sec_label_refresh_errors: dict[int, str] = field(default_factory=dict)


def filer_category(category: str | None, entity_type: str | None, files_10q: bool, annual_form: str | None = None) -> str:
    """Deadline class. A company whose annual report is a 20-F or 40-F is a foreign private issuer whatever its
    accelerated status says: the 20-F deadline is four months regardless."""
    base_annual = (annual_form or "").strip().removesuffix("/A")
    if base_annual in ("20-F", "40-F"):
        return "foreign_private_issuer"
    text = (category or "").lower()
    if "large accelerated" in text:
        return "large_accelerated"
    if "accelerated filer" in text and "non-accelerated" not in text:
        return "accelerated"
    if not files_10q and (entity_type or "").lower() != "operating":
        return "foreign_private_issuer"
    return "non_accelerated"


def next_business_day(d: date) -> date:
    while d.weekday() >= 5:  # Saturday, Sunday; federal holidays are not modelled
        d += timedelta(days=1)
    return d


def statutory_deadline(period_end: date, category: str, kind: str) -> date | None:
    rule = DEADLINES[category][kind]
    if rule is None:
        return None
    unit, n = rule
    raw = fiscal.add_months(period_end, n) if unit == "months" else period_end + timedelta(days=n)
    return next_business_day(raw)


def run_checks(conn: psycopg.Connection, facts: RunFacts, overrides, today: date) -> list[CheckResult]:
    accepted = overrides.accepted_findings
    results = [
        check_run_complete(facts),
        check_http_health(facts),
        check_tickers_resolved(facts),
        check_cik_changes_acknowledged(conn, facts),
        check_fiscal_year_end_known(conn),
        check_history_gap(facts),
        check_no_filings_lost(conn, facts),
        check_labels_complete(conn),
        check_labels_unique(conn),
        check_labels_unique_across_ciks(conn),
        check_labels_inferred_for_amendments(conn),
        check_labels_far_from_calendar(conn),
        check_overdue_reports(conn, today),
        check_labels_match_sec(conn, overrides.naming_conventions, today),
        check_news(conn, facts),
    ]
    results = [apply_accepted(r, accepted) for r in results]
    with conn.cursor() as cur:
        for r in results:
            cur.execute(
                "INSERT INTO checks (run_id, name, severity, passed, summary, detail) VALUES (%s, %s, %s, %s, %s, %s)",
                (facts.run_id, r.name, r.severity, r.passed, r.summary, Jsonb(r.detail)),
            )
    conn.commit()
    return results


def apply_accepted(result: CheckResult, accepted: list[dict]) -> CheckResult:
    """Remove the items a person accepted in overrides.toml; the full item list is evaluated, never a truncation."""
    keys = {str(a.get("key")) for a in accepted if a.get("check") == result.name}
    if result.passed or not keys:
        return result
    items = result.detail.get("items")
    if not isinstance(items, list):
        return result
    remaining = [i for i in items if str(i.get("key") if isinstance(i, dict) else i) not in keys]
    accepted_items = [i for i in items if i not in remaining]
    if not accepted_items:
        return result
    detail = {**result.detail, "items": remaining, "accepted": accepted_items}
    if remaining:
        return CheckResult(result.name, result.severity, False, f"{result.summary} ({len(accepted_items)} accepted)", detail)
    return CheckResult(result.name, "info", True, f"{result.summary} (all {len(accepted_items)} accepted in overrides.toml)", detail)


# ----------------------------------------------------------------------------- the checks

def check_run_complete(f: RunFacts) -> CheckResult:
    ok = f.ciks_fetched == f.ciks_expected and f.ciks_expected > 0 and not f.fetch_errors
    errors = [{"key": str(cik), "cik": cik, "error": msg} for cik, msg in f.fetch_errors.items()]
    return CheckResult(
        "run_complete", "hard", ok,
        f"fetched and validated {f.ciks_fetched} of {f.ciks_expected} filer pages" + (f"; {len(errors)} failed" if errors else ""),
        {"ciks_expected": f.ciks_expected, "ciks_fetched": f.ciks_fetched, "items": errors},
    )


def check_http_health(f: RunFacts) -> CheckResult:
    over_budget = f.duration_sec > f.time_budget_sec
    ok = f.http_errors == 0 and not over_budget
    return CheckResult(
        "http_health", "soft", ok,
        f"{f.http_errors} EDGAR errors, {f.retries} retries, {f.duration_sec:.0f}s of a {f.time_budget_sec}s budget",
        {"http_errors": f.http_errors, "retries": f.retries, "duration_sec": round(f.duration_sec, 1)},
    )


def check_tickers_resolved(f: RunFacts) -> CheckResult:
    ok = not f.missing_tickers
    by_override = [{"ticker": t, "cik": c} for t, c in f.resolved_by_override.items()]
    summary = "every ticker is in company_tickers.json" if ok else f"not in company_tickers.json and no current CIK override: {', '.join(f.missing_tickers)}"
    if by_override:
        summary += f"; {len(by_override)} resolved by an evidenced override ({', '.join(x['ticker'] for x in by_override)})"
    return CheckResult("tickers_resolved", "hard", ok, summary, {"items": [{"key": t} for t in f.missing_tickers], "resolved_by_override": by_override})


def check_cik_changes_acknowledged(conn: psycopg.Connection, f: RunFacts) -> CheckResult:
    """A ticker whose CIK moved keeps its old CIK until a person records it in overrides.toml."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cc.ticker, cc.cik, cc.note FROM company_ciks cc JOIN companies c ON c.ticker = cc.ticker
            WHERE c.active AND cc.source = 'sec_tickers' AND NOT cc.is_current ORDER BY cc.ticker
            """
        )
        stale = cur.fetchall()
    items = [{"key": f"{r['ticker']}/{r['cik']}", "ticker": r["ticker"], "previous_cik": r["cik"]} for r in stale]
    for ticker, old, new in f.changed_tickers:
        items.append({"key": f"{ticker}/{old}", "ticker": ticker, "previous_cik": old, "new_cik": new, "changed_this_run": True})
    seen: set[str] = set()
    items = [i for i in items if not (i["key"] in seen or seen.add(i["key"]))]
    ok = not items
    return CheckResult(
        "cik_changes_acknowledged", "hard", ok,
        "no ticker changed CIK" if ok else f"{len(items)} ticker(s) moved to a new CIK; record the old one in overrides.toml [[extra_ciks]]",
        {"items": items},
    )


def check_fiscal_year_end_known(conn: psycopg.Connection) -> CheckResult:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT cc.ticker, fl.cik, fl.name FROM filers fl
            JOIN company_ciks cc ON cc.cik = fl.cik JOIN companies c ON c.ticker = cc.ticker
            WHERE c.active AND fl.fiscal_year_end_override IS NULL
              AND (fl.fiscal_year_end IS NULL OR fl.fiscal_year_end !~ '^[0-9]{4}$')
            """
        )
        rows = cur.fetchall()
    items = [{"key": f"{r['ticker']}/{r['cik']}", "name": r["name"]} for r in rows]
    return CheckResult(
        "fiscal_year_end_known", "hard", not items,
        "every filer has a fiscal year end" if not items else f"{len(items)} filer(s) without a fiscal year end; add [[fiscal_year_ends]] to overrides.toml",
        {"items": items},
    )


def check_history_gap(f: RunFacts) -> CheckResult:
    """If the previous successful run is older than the oldest filing on today's recent page, filings may have
    entered and left the page unseen: a backfill is needed before the table can be called complete."""
    if f.previous_ok_run_started is None:
        return CheckResult("history_gap", "hard", True, "first successful run; nothing to compare with", {})
    items = [{"key": str(cik), "cik": cik, "recent_page_starts": str(ws), "previous_ok_run": str(f.previous_ok_run_started)}
             for cik, ws in f.window_starts.items() if ws is not None and ws > f.previous_ok_run_started]
    return CheckResult(
        "history_gap", "hard", not items,
        f"previous successful run {f.previous_ok_run_started} is inside every filer's recent page" if not items
        else f"{len(items)} filer(s) whose recent page starts after the previous successful run ({f.previous_ok_run_started}); run `sec-filings backfill --full`",
        {"items": items},
    )


def check_no_filings_lost(conn: psycopg.Connection, f: RunFacts) -> CheckResult:
    """Every filing the feed stopped listing stays a finding until it reappears or a person accepts it."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cc.ticker, fl.cik, fl.accession_number, fl.form, fl.filing_date, fl.not_seen_since
            FROM filings fl JOIN company_ciks cc ON cc.cik = fl.cik
            WHERE fl.not_seen_since IS NOT NULL ORDER BY fl.not_seen_since DESC, cc.ticker
            """
        )
        rows = cur.fetchall()
    new_keys = {acc for accs in f.newly_missing.values() for acc in accs}
    items = [{"key": r["accession_number"], "ticker": r["ticker"], "cik": r["cik"], "form": r["form"], "filed": str(r["filing_date"]),
              "not_seen_since": str(r["not_seen_since"]), "new_this_run": r["accession_number"] in new_keys} for r in rows]
    ok = not items
    return CheckResult(
        "no_filings_lost", "hard", ok,
        "no filing has disappeared from the EDGAR feed" if ok
        else f"{len(items)} filing(s) no longer listed by EDGAR ({len(new_keys)} new this run); post-acceptance deletion? decide and accept, or wait for them to reappear",
        {"items": items},
    )


def check_labels_complete(conn: psycopg.Connection) -> CheckResult:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT accession_number, cik, form, report_date, label_method FROM filings
            WHERE family IN ('annual', 'quarterly')
              AND (fiscal_year IS NULL OR fiscal_period IS NULL OR label_method IS NULL
                   OR label_method NOT IN ('rule', 'rule_filing_date', 'rule_far', 'corrected')
                   OR fiscal_period NOT IN ('FY', 'Q1', 'Q2', 'Q3', 'Q4'))
            ORDER BY cik, filing_date
            """
        )
        rows = cur.fetchall()
    items = [{"key": r["accession_number"], "cik": r["cik"], "form": r["form"], "why": r["label_method"]} for r in rows]
    return CheckResult(
        "labels_complete", "hard", not items,
        "every report has a fiscal label" if not items else f"{len(items)} report(s) without a usable fiscal label",
        {"items": items, "count": len(items)},
    )


def check_labels_unique(conn: psycopg.Connection) -> CheckResult:
    """Two original (non-amendment) reports for the same company, year and period under the same CIK."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cc.ticker, fl.cik, fl.family, fl.fiscal_year, fl.fiscal_period,
                   array_agg(fl.accession_number ORDER BY fl.filing_date) AS accessions,
                   array_agg(fl.form ORDER BY fl.filing_date) AS forms
            FROM filings fl JOIN company_ciks cc ON cc.cik = fl.cik
            WHERE fl.family IN ('annual', 'quarterly') AND NOT fl.is_amendment AND fl.fiscal_year IS NOT NULL
            GROUP BY cc.ticker, fl.cik, fl.family, fl.fiscal_year, fl.fiscal_period HAVING count(*) > 1
            ORDER BY cc.ticker, fl.fiscal_year
            """
        )
        rows = cur.fetchall()
    items = [
        {"key": f"{r['ticker']}/{r['cik']}/{r['family']}/{r['fiscal_year']}/{r['fiscal_period']}",
         "accessions": r["accessions"], "forms": r["forms"]} for r in rows
    ]
    return CheckResult(
        "labels_unique", "hard", not items,
        "one original report per company, year and period" if not items
        else f"{len(items)} (company, year, period) with more than one original report; look at each",
        {"items": items},
    )


def check_labels_unique_across_ciks(conn: psycopg.Connection) -> CheckResult:
    """The same company, year and period reported as originals by two different CIKs of that company (reorganisations)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cc.ticker, fl.family, fl.fiscal_year, fl.fiscal_period,
                   array_agg(DISTINCT fl.cik) AS ciks, array_agg(fl.accession_number ORDER BY fl.filing_date) AS accessions
            FROM filings fl JOIN company_ciks cc ON cc.cik = fl.cik
            WHERE fl.family IN ('annual', 'quarterly') AND NOT fl.is_amendment AND fl.fiscal_year IS NOT NULL
            GROUP BY cc.ticker, fl.family, fl.fiscal_year, fl.fiscal_period
            HAVING count(DISTINCT fl.cik) > 1 ORDER BY cc.ticker, fl.fiscal_year
            """
        )
        rows = cur.fetchall()
    items = [{"key": f"{r['ticker']}/{r['family']}/{r['fiscal_year']}/{r['fiscal_period']}", "ciks": r["ciks"], "accessions": r["accessions"]} for r in rows]
    return CheckResult(
        "labels_unique_across_ciks", "soft", not items,
        "no period reported by two CIKs of one company" if not items
        else f"{len(items)} period(s) reported as originals by two CIKs of the same company; the reports view shows the current CIK's and lists the other",
        {"items": items},
    )


def check_labels_inferred_for_amendments(conn: psycopg.Connection) -> CheckResult:
    """An amendment whose period of report is the filing date gets its label by inference (the last period end
    before the filing date), which can point at the wrong year for an amendment filed long after its period.
    Each such case needs a person to read the document and accept it."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cc.ticker, fl.accession_number, fl.form, fl.filing_date, fl.report_date, fl.fiscal_year, fl.fiscal_period, fl.primary_document_url
            FROM filings fl JOIN company_ciks cc ON cc.cik = fl.cik
            WHERE fl.label_method = 'rule_filing_date' AND fl.is_amendment ORDER BY cc.ticker, fl.filing_date
            """
        )
        rows = cur.fetchall()
    items = [{"key": r["accession_number"], "ticker": r["ticker"], "form": r["form"], "filed": str(r["filing_date"]),
              "label": f"{r['fiscal_year']} {r['fiscal_period']}", "document": r["primary_document_url"]} for r in rows]
    return CheckResult(
        "labels_inferred_for_amendments", "hard", not items,
        "no amendment labelled by inference" if not items
        else f"{len(items)} amendment(s) labelled from the filing date; read each document and accept the label in overrides.toml",
        {"items": items},
    )


def check_labels_far_from_calendar(conn: psycopg.Connection) -> CheckResult:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cc.ticker, fl.accession_number, fl.form, fl.filing_date, fl.report_date, fl.fiscal_year, fl.fiscal_period,
                   fl.label_method, fl.label_note
            FROM filings fl JOIN company_ciks cc ON cc.cik = fl.cik
            WHERE fl.label_method IN ('rule_far', 'rule_filing_date') ORDER BY cc.ticker, fl.filing_date
            """
        )
        rows = cur.fetchall()
    items = [{"key": r["accession_number"], "ticker": r["ticker"], "form": r["form"], "filed": str(r["filing_date"]),
              "report_date": str(r["report_date"]), "label": f"{r['fiscal_year']} {r['fiscal_period']}",
              "method": r["label_method"], "note": r["label_note"]} for r in rows]
    far = [i for i in items if i["method"] == "rule_far"]
    return CheckResult(
        "labels_far_from_calendar", "soft", not far,
        "every period end sits on the company's fiscal calendar" if not items
        else f"{len(items)} filing(s) not labelled straight from the period of report: {len(items) - len(far)} from the "
             f"filing date (EDGAR quirk), {len(far)} off the fiscal calendar (best effort)",
        {"items": items},
    )


def check_overdue_reports(conn: psycopg.Connection, today: date) -> CheckResult:
    """For each company: the next report the calendar expects, its statutory deadline, and whether it is overdue."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.ticker, fl.cik, fl.name, fl.category, fl.entity_type,
                   coalesce(fl.fiscal_year_end_override, fl.fiscal_year_end) AS fye,
                   (SELECT max(report_date) FROM filings x WHERE x.cik = fl.cik AND x.family IN ('annual','quarterly') AND NOT x.is_amendment) AS last_report,
                   (SELECT count(*) > 0 FROM filings x WHERE x.cik = fl.cik AND x.family = 'quarterly') AS files_10q,
                   (SELECT x.form FROM filings x WHERE x.cik = fl.cik AND x.family = 'annual' AND NOT x.is_amendment
                      ORDER BY x.report_date DESC NULLS LAST LIMIT 1) AS annual_form
            FROM companies c JOIN company_ciks cc ON cc.ticker = c.ticker AND cc.is_current
            JOIN filers fl ON fl.cik = cc.cik WHERE c.active ORDER BY c.ticker
            """
        )
        rows = cur.fetchall()
        cur.execute("SELECT cik, form, report_date, filing_date FROM filings WHERE family = 'late_notice'")
        notices = cur.fetchall()
    items, expected = [], []
    for r in rows:
        if not r["last_report"] or not fiscal.valid_mmdd(r["fye"]):
            continue
        cat = filer_category(r["category"], r["entity_type"], r["files_10q"], r["annual_form"])
        fye_month, fye_day = int(r["fye"][:2]), int(r["fye"][2:])
        year_ends = [fiscal.fye_date(y, r["fye"]) for y in range(r["last_report"].year, r["last_report"].year + 3)]
        if cat == "foreign_private_issuer" or not r["files_10q"]:
            next_end = next(d for d in year_ends if d > r["last_report"] + timedelta(days=PERIOD_MATCH_DAYS))
            kind = "annual"
        else:
            nominal_fye = fiscal.fye_date(r["last_report"].year, r["fye"])
            if fiscal.is_month_end(nominal_fye):
                ends: list[date] = []
                for y in range(r["last_report"].year, r["last_report"].year + 3):
                    ends.extend(fiscal.nominal_period_ends(r["fye"], y))
                nearest = min(ends, key=lambda d: abs((d - r["last_report"]).days))
                next_end = next(d for d in ends if d > nearest)
            else:
                next_end = r["last_report"] + timedelta(weeks=13)   # 52/53-week calendar: thirteen weeks on
            is_year_end = any(abs((next_end - ye).days) <= PERIOD_MATCH_DAYS for ye in year_ends)
            kind = "annual" if is_year_end else "quarterly"
        deadline = statutory_deadline(next_end, cat, kind)
        if deadline is None:
            continue
        notice = next((n for n in notices if n["cik"] == r["cik"] and n["report_date"]
                       and abs((n["report_date"] - next_end).days) <= PERIOD_MATCH_DAYS
                       and (("10-K" in n["form"] or "20-F" in n["form"]) == (kind == "annual"))), None)
        timely_notice = bool(notice and notice["filing_date"] <= next_business_day(deadline + timedelta(days=1)))
        if timely_notice:
            deadline = next_business_day(deadline + timedelta(days=NT_GRACE[kind]))
        entry = {"key": f"{r['ticker']}/{kind}/{next_end}", "ticker": r["ticker"], "expected": kind,
                 "period_end": str(next_end), "deadline": str(deadline), "filer_category": cat,
                 "late_notice": (str(notice["filing_date"]) if notice else None), "notice_timely": timely_notice,
                 "last_report": str(r["last_report"])}
        expected.append(entry)
        if today > deadline:
            items.append({**entry, "days_overdue": (today - deadline).days})
    return CheckResult(
        "overdue_reports", "soft", not items,
        "no company is past its filing deadline" if not items
        else f"{len(items)} company report(s) past the statutory deadline (or EDGAR has not shown them yet)",
        {"items": items, "expected_next": expected},
    )


def check_labels_match_sec(conn: psycopg.Connection, conventions: dict[str, dict], today: date) -> CheckResult:
    """Our label versus the SEC's XBRL label. A difference is a naming convention only for a company listed in
    overrides.toml [[naming_conventions]], in the stated direction and period; everything else is a real
    disagreement that a person must look at. Filings without a SEC label yet are counted, not hidden."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM sec_labels")
        n_labels = cur.fetchone()["n"]
        cur.execute(
            """
            SELECT cc.ticker, fl.accession_number, fl.form, fl.filing_date, fl.report_date, fl.fiscal_year, fl.fiscal_period,
                   s.fiscal_year AS sec_year, s.fiscal_period AS sec_period
            FROM filings fl JOIN sec_labels s USING (accession_number) JOIN company_ciks cc ON cc.cik = fl.cik
            WHERE fl.family IN ('annual', 'quarterly')
              AND (fl.fiscal_year IS DISTINCT FROM s.fiscal_year OR fl.fiscal_period IS DISTINCT FROM s.fiscal_period)
            ORDER BY cc.ticker, fl.report_date
            """
        )
        rows = cur.fetchall()
        cur.execute("SELECT count(*) AS n FROM filings fl JOIN sec_labels s USING (accession_number)")
        compared = cur.fetchone()["n"]
        cur.execute(
            """
            SELECT count(*) FILTER (WHERE fl.filing_date > %s) AS pending,
                   count(*) FILTER (WHERE fl.filing_date <= %s) AS unlabelled
            FROM filings fl LEFT JOIN sec_labels s USING (accession_number)
            WHERE fl.family IN ('annual', 'quarterly') AND s.accession_number IS NULL AND coalesce(fl.is_xbrl, false)
            """,
            (today - timedelta(days=SEC_LABEL_PENDING_DAYS), today - timedelta(days=SEC_LABEL_PENDING_DAYS)),
        )
        cov = cur.fetchone()
    if n_labels == 0:
        return CheckResult("labels_match_sec", "info", True, "no SEC labels loaded yet (run sec-filings verify-labels)", {})

    def is_convention(r) -> bool:
        c = conventions.get(r["ticker"])
        if not c or r["sec_year"] is None or r["fiscal_year"] is None:
            return False
        if r["sec_year"] - r["fiscal_year"] != c["sec_year_offset"] or r["fiscal_period"] != r["sec_period"]:
            return False
        return c["until"] is None or r["filing_date"] <= c["until"]

    convention = [r for r in rows if is_convention(r)]
    anomalies = [r for r in rows if not is_convention(r)]
    by_ticker = Counter(r["ticker"] for r in convention)
    items = [{"key": r["accession_number"], "ticker": r["ticker"], "form": r["form"], "report_date": str(r["report_date"]),
              "ours": f"{r['fiscal_year']} {r['fiscal_period']}", "sec": f"{r['sec_year']} {r['sec_period']}"} for r in anomalies]
    naming = ", ".join(f"{t} {n}" for t, n in sorted(by_ticker.items()))
    summary = f"{len(items)} real disagreement(s) with the SEC's XBRL label on {compared} filings compared"
    if convention:
        summary += f"; {len(convention)} are documented naming conventions ({naming})"
    summary += f"; {cov['pending']} recent XBRL filing(s) awaiting a SEC label, {cov['unlabelled']} older without one"
    return CheckResult(
        "labels_match_sec", "hard", not items, summary,
        {"items": items, "compared": compared, "naming_convention_counts": dict(by_ticker),
         "pending_sec_label": cov["pending"], "older_without_sec_label": cov["unlabelled"]},
    )


def check_news(conn: psycopg.Connection, f: RunFacts) -> CheckResult:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cc.ticker, fl.accession_number, fl.form, fl.filing_date, fl.report_date, fl.fiscal_year, fl.fiscal_period, fl.family, fl.is_amendment
            FROM filings fl JOIN company_ciks cc ON cc.cik = fl.cik
            WHERE fl.first_seen_run = %s ORDER BY cc.ticker, fl.filing_date
            """,
            (f.run_id,),
        )
        rows = cur.fetchall()
    new = [{"key": r["accession_number"], "ticker": r["ticker"], "form": r["form"], "filed": str(r["filing_date"]),
            "period": str(r["report_date"]), "label": f"{r['fiscal_year']} {r['fiscal_period']}"} for r in rows]
    notable = [n for n, r in zip(new, rows) if r["is_amendment"] or r["family"] == "late_notice"]
    summary = f"{len(new)} new filing(s) this run, of which {len(notable)} amendment(s) or late-filing notice(s)"
    if f.sec_labels_refreshed:
        summary += f"; SEC labels refreshed for {len(f.sec_labels_refreshed)} filer(s)"
    if f.sec_label_refresh_errors:
        summary += f"; SEC label refresh failed for {len(f.sec_label_refresh_errors)} filer(s)"
    return CheckResult("news", "info", True, summary,
                       {"new": new[:200], "notable": notable, "sec_labels_refreshed": f.sec_labels_refreshed,
                        "sec_label_refresh_errors": {str(k): v for k, v in f.sec_label_refresh_errors.items()}})


def format_results(results: list[CheckResult]) -> str:
    lines = []
    for r in results:
        mark = {"hard": "FAIL" if not r.passed else "ok", "soft": "WARN" if not r.passed else "ok", "info": "info"}[r.severity]
        lines.append(f"[{mark:4}] {r.name}: {r.summary}")
        if not r.passed or r.severity == "info":
            items = r.detail.get("items") or r.detail.get("notable") or []
            for item in items[:10]:
                lines.append(f"        - {item}")
            if len(items) > 10:
                lines.append(f"        ... {len(items) - 10} more")
    return "\n".join(lines)


def hard_failures(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if r.severity == "hard" and not r.passed]
