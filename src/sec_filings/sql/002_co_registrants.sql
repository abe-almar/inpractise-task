-- A filing can be submitted by several registrants under one accession number (Exxon's Q2 2026 10-Q is listed
-- under both the new holding company and Exxon Mobil Corp). The row keeps the first CIK that listed it; the others
-- are recorded here so nothing is counted twice and nothing is lost.
ALTER TABLE filings ADD COLUMN IF NOT EXISTS also_filed_by_ciks bigint[] NOT NULL DEFAULT '{}';

-- The reports view now also shows the SEC's own XBRL label for the filing (when verify-labels loaded one), because
-- some issuers name fiscal years by the calendar year in which they START (GameStop, Toyota and Walmart in older years).
DROP VIEW IF EXISTS reports;
CREATE VIEW reports AS
WITH f AS (
    SELECT fl.*, cc.ticker, cc.is_current AS cik_is_current
    FROM filings fl
    JOIN company_ciks cc ON cc.cik = fl.cik
    WHERE fl.family IN ('annual', 'quarterly') AND fl.fiscal_year IS NOT NULL
),
originals AS (
    SELECT f.*,
           row_number() OVER (PARTITION BY ticker, family, fiscal_year, fiscal_period
                              ORDER BY cik_is_current DESC, filing_date, accession_number) AS rn
    FROM f
    WHERE NOT is_amendment
)
SELECT o.ticker, o.family, o.fiscal_year, o.fiscal_period, o.form, o.accession_number, o.cik, o.also_filed_by_ciks,
       o.filing_date, o.report_date, o.primary_document_url, o.label_method, o.is_transition, o.not_seen_since,
       s.fiscal_year AS sec_fiscal_year, s.fiscal_period AS sec_fiscal_period,
       (SELECT count(*) FROM f a WHERE a.ticker = o.ticker AND a.family = o.family
          AND a.fiscal_year = o.fiscal_year AND a.fiscal_period = o.fiscal_period AND a.is_amendment) AS amendments,
       (SELECT array_agg(a.accession_number ORDER BY a.filing_date) FROM f a WHERE a.ticker = o.ticker
          AND a.family = o.family AND a.fiscal_year = o.fiscal_year AND a.fiscal_period = o.fiscal_period
          AND a.is_amendment) AS amendment_accessions,
       (SELECT array_agg(x.accession_number ORDER BY x.filing_date) FROM originals x WHERE x.ticker = o.ticker
          AND x.family = o.family AND x.fiscal_year = o.fiscal_year AND x.fiscal_period = o.fiscal_period
          AND x.rn > 1) AS other_originals
FROM originals o
LEFT JOIN sec_labels s ON s.accession_number = o.accession_number
WHERE o.rn = 1;
