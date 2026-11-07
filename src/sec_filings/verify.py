"""Cross-check our fiscal labels against the SEC's own, taken from the XBRL companyfacts API.

Every XBRL fact carries the fiscal year (`fy`) and period (`fp`) of the filing it came from. We take, per accession
number, the label its facts agree on. This exists only for XBRL-era filings (roughly 2009 onwards), so it is a
cross-check, not the source of truth; disagreements are stored and reported by the morning check, never applied."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

import psycopg

from sec_filings import fiscal, loader
from sec_filings.edgar import EdgarClient, EdgarNotFound

log = logging.getLogger(__name__)


def sec_labels_from_companyfacts(data: dict) -> dict[str, tuple[int | None, str | None, str]]:
    votes: dict[str, Counter] = defaultdict(Counter)
    forms: dict[str, str] = {}
    for taxonomy in (data.get("facts") or {}).values():
        for concept in taxonomy.values():
            for facts in (concept.get("units") or {}).values():
                for fact in facts:
                    form = fact.get("form") or ""
                    if fiscal.family(form) not in ("annual", "quarterly"):
                        continue
                    accn = fact.get("accn")
                    if not accn:
                        continue
                    votes[accn][(fact.get("fy"), fact.get("fp"))] += 1
                    forms[accn] = form
    out = {}
    for accn, counter in votes.items():
        (fy, fp), _ = counter.most_common(1)[0]
        out[accn] = (int(fy) if fy is not None else None, str(fp) if fp else None, forms[accn])
    return out


def verify_labels(conn: psycopg.Connection, client: EdgarClient) -> dict:
    pairs = loader.active_ciks(conn)
    stored = 0
    skipped = []
    for ticker, cik in pairs:
        try:
            data, _ = client.companyfacts(cik)
        except EdgarNotFound:
            skipped.append(f"{ticker}/{cik}")
            log.info("%s CIK %s: no companyfacts (no XBRL filings)", ticker, cik)
            continue
        labels = sec_labels_from_companyfacts(data)
        with conn.cursor() as cur:
            for accn, (fy, fp, form) in labels.items():
                cur.execute(
                    """
                    INSERT INTO sec_labels (accession_number, fiscal_year, fiscal_period, form, source, fetched_at)
                    VALUES (%s, %s, %s, %s, 'companyfacts', now())
                    ON CONFLICT (accession_number) DO UPDATE SET fiscal_year = EXCLUDED.fiscal_year,
                        fiscal_period = EXCLUDED.fiscal_period, form = EXCLUDED.form, fetched_at = now()
                    """,
                    (accn, fy, fp, form),
                )
        conn.commit()
        stored += len(labels)
        log.info("%s CIK %s: %d SEC labels", ticker, cik, len(labels))
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS compared,
                   count(*) FILTER (WHERE fl.fiscal_year IS DISTINCT FROM s.fiscal_year
                                        OR fl.fiscal_period IS DISTINCT FROM s.fiscal_period) AS disagree
            FROM filings fl JOIN sec_labels s USING (accession_number) WHERE fl.family IN ('annual', 'quarterly')
            """
        )
        row = cur.fetchone()
    return {"labels_stored": stored, "compared": row["compared"], "disagree": row["disagree"], "skipped": skipped}
