"""Answers for humans: the one-row question and a per-company summary."""

from __future__ import annotations

import psycopg


def answer(conn: psycopg.Connection, ticker: str, fiscal_year: int, period: str = "FY") -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker, family, fiscal_year, fiscal_period, form, accession_number, cik, also_filed_by_ciks, filing_date,
                   report_date, report_date_corrected, primary_document_url, label_method, note, sec_fiscal_year, sec_fiscal_period, amendments,
                   amendment_accessions, other_originals, not_seen_since
            FROM reports WHERE ticker = %s AND fiscal_year = %s AND fiscal_period = %s
            """,
            (ticker.upper(), fiscal_year, period.upper()),
        )
        return cur.fetchall()


def summary(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.ticker,
                   string_agg(DISTINCT cc.cik::text, ',' ORDER BY cc.cik::text) AS ciks,
                   max(fl.name) FILTER (WHERE cc.is_current) AS name,
                   count(*) FILTER (WHERE f.family = 'annual' AND NOT f.is_amendment) AS annual,
                   count(*) FILTER (WHERE f.family = 'quarterly' AND NOT f.is_amendment) AS quarterly,
                   count(*) FILTER (WHERE f.is_amendment) AS amendments,
                   count(*) FILTER (WHERE f.family = 'late_notice') AS late_notices,
                   min(f.report_date) FILTER (WHERE f.family = 'annual') AS first_annual,
                   max(f.report_date) FILTER (WHERE f.family IN ('annual','quarterly')) AS last_period,
                   count(*) FILTER (WHERE f.family IN ('annual','quarterly') AND f.label_method <> 'rule') AS odd_labels
            FROM companies c
            JOIN company_ciks cc ON cc.ticker = c.ticker
            LEFT JOIN filers fl ON fl.cik = cc.cik
            LEFT JOIN filings f ON f.cik = cc.cik
            WHERE c.active GROUP BY c.ticker ORDER BY c.ticker
            """
        )
        return cur.fetchall()


def latest_run(conn: psycopg.Connection) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
        return cur.fetchone()


def previous_clean_run(conn: psycopg.Connection, before_run_id: int | None = None) -> dict | None:
    """The last run that completed AND passed its hard checks: the only kind that counts as coverage."""
    with conn.cursor() as cur:
        if before_run_id is None:
            cur.execute("SELECT * FROM runs WHERE status = 'ok' AND checks_passed IS TRUE ORDER BY id DESC LIMIT 1")
        else:
            cur.execute("SELECT * FROM runs WHERE status = 'ok' AND checks_passed IS TRUE AND id < %s ORDER BY id DESC LIMIT 1", (before_run_id,))
        return cur.fetchone()


previous_ok_run = previous_clean_run  # old name


def checks_of_run(conn: psycopg.Connection, run_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT name, severity, passed, summary, detail FROM checks WHERE run_id = %s ORDER BY id", (run_id,))
        return cur.fetchall()
