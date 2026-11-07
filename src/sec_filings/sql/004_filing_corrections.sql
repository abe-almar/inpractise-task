-- Per-filing corrections of EDGAR's "period of report", from overrides.toml [[filing_corrections]], each with its
-- evidence. Found by the fresh-agent test (a Disney 10-Q whose EDGAR period is a year off) and by reading an
-- Apple 10-Q/A of 1997. EDGAR's own value stays in report_date; the label is derived from the corrected one.
ALTER TABLE filings ADD COLUMN IF NOT EXISTS report_date_corrected date;
ALTER TABLE filings ADD COLUMN IF NOT EXISTS correction_note text;

DROP VIEW IF EXISTS reports;
CREATE VIEW reports AS
WITH registrants AS (
    SELECT accession_number, cik FROM filings
    UNION
    SELECT accession_number, unnest(also_filed_by_ciks) FROM filings
),
f AS (
    SELECT DISTINCT ON (cc.ticker, fl.accession_number)
           fl.*, cc.ticker, cc.is_current AS cik_is_current
    FROM filings fl
    JOIN registrants r ON r.accession_number = fl.accession_number
    JOIN company_ciks cc ON cc.cik = r.cik
    WHERE fl.family IN ('annual', 'quarterly') AND fl.fiscal_year IS NOT NULL
    ORDER BY cc.ticker, fl.accession_number, cc.is_current DESC
),
originals AS (
    SELECT f.*,
           row_number() OVER (PARTITION BY ticker, family, fiscal_year, fiscal_period
                              ORDER BY cik_is_current DESC, filing_date, accession_number) AS rn
    FROM f
    WHERE NOT is_amendment
)
SELECT o.ticker, o.family, o.fiscal_year, o.fiscal_period, o.form, o.accession_number, o.cik, o.also_filed_by_ciks,
       o.filing_date, o.report_date, o.report_date_corrected, o.primary_document_url, o.label_method, o.is_transition,
       o.not_seen_since, o.note,
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
