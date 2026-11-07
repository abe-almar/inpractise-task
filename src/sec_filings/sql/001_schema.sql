-- The whole schema in one file, applied once by sec_filings.db.migrate (later changes go in 002_*.sql files).
-- Read top to bottom: runs, the company list and its CIKs, what EDGAR says about each filer, the filings, the
-- SEC's own labels, the checks, the raw page snapshots, and the one view that answers questions.

-- One row per execution of the job. checks_passed stays NULL until the checks ran.
CREATE TABLE IF NOT EXISTS runs (
    id               bigserial PRIMARY KEY,
    mode             text NOT NULL,                     -- 'daily' | 'backfill'
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    status           text NOT NULL DEFAULT 'running',   -- 'running' | 'ok' | 'failed'
    checks_passed    boolean,
    requests         integer NOT NULL DEFAULT 0,
    retries          integer NOT NULL DEFAULT 0,
    http_errors      integer NOT NULL DEFAULT 0,
    ciks_expected    integer NOT NULL DEFAULT 0,
    ciks_fetched     integer NOT NULL DEFAULT 0,
    filings_seen     integer NOT NULL DEFAULT 0,
    filings_inserted integer NOT NULL DEFAULT 0,
    filings_updated  integer NOT NULL DEFAULT 0,
    error            text,
    notes            jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- The company list (companies.csv). A company that leaves the list is deactivated, never deleted.
CREATE TABLE IF NOT EXISTS companies (
    ticker     text PRIMARY KEY,
    active     boolean NOT NULL DEFAULT true,
    added_at   timestamptz NOT NULL DEFAULT now(),
    note       text
);

-- A company may own several CIKs over time (reorganisations, SPAC shells). Every CIK here is fetched.
CREATE TABLE IF NOT EXISTS company_ciks (
    ticker      text NOT NULL REFERENCES companies(ticker),
    cik         bigint NOT NULL,
    source      text NOT NULL,                  -- 'sec_tickers' (company_tickers.json) | 'override' (overrides.toml)
    is_current  boolean NOT NULL DEFAULT false, -- the CIK the ticker resolves to today
    valid_from  date,
    valid_to    date,
    note        text,
    evidence    text,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, cik)
);

-- What company_tickers.json said for each ticker at each run, so a change is visible and dated.
CREATE TABLE IF NOT EXISTS ticker_cik_map (
    run_id  bigint NOT NULL REFERENCES runs(id),
    ticker  text NOT NULL,
    cik     bigint,
    title   text,
    PRIMARY KEY (run_id, ticker)
);

-- What EDGAR says about each CIK, plus the job's bookkeeping per filer.
CREATE TABLE IF NOT EXISTS filers (
    cik                       bigint PRIMARY KEY,
    name                      text,
    entity_type               text,
    category                  text,              -- filer category as EDGAR gives it today (not historical)
    sic                       text,
    sic_description           text,
    fiscal_year_end           text,              -- MMDD from EDGAR, may be NULL
    fiscal_year_end_override  text,              -- MMDD from overrides.toml
    state_of_incorporation    text,
    tickers                   jsonb,
    exchanges                 jsonb,
    former_names              jsonb,
    fetched_at                timestamptz,
    last_run_id               bigint,
    recent_window_start       date,              -- oldest filing date on the recent page at the last fetch
    backfill_completed_at     timestamptz,       -- history pages were read at least once
    coverage_through          date,              -- filings known complete through this date (the watermark)
    coverage_gap_since        date,              -- set when the recent page started after the watermark; cleared by a history read
    sec_labels_attempted_at   timestamptz        -- last attempt to load the SEC's XBRL labels (refresh queue order)
);

-- One row per tracked filing. The accession number is EDGAR's key; the first registrant that listed the filing owns
-- the row, other registrants are recorded in also_filed_by_ciks. Rows are never deleted.
CREATE TABLE IF NOT EXISTS filings (
    accession_number         text PRIMARY KEY,
    cik                      bigint NOT NULL REFERENCES filers(cik),
    also_filed_by_ciks       bigint[] NOT NULL DEFAULT '{}',
    form                     text NOT NULL,
    family                   text NOT NULL,      -- 'annual' | 'quarterly' | 'late_notice'
    is_amendment             boolean NOT NULL,
    is_transition            boolean NOT NULL DEFAULT false,
    filing_date              date NOT NULL,
    report_date              date,               -- EDGAR's "period of report", kept as given
    report_date_corrected    date,               -- from overrides.toml [[filing_corrections]], with evidence
    correction_note          text,
    acceptance_datetime      timestamptz,
    act                      text,
    file_number              text,
    film_number              text,
    primary_document         text,
    primary_doc_description  text,
    primary_document_url     text,
    is_xbrl                  boolean,
    is_inline_xbrl           boolean,
    size_bytes               bigint,
    fiscal_year              integer,
    fiscal_period            text,               -- 'FY', 'Q1', 'Q2', 'Q3' (or 'Q4' when something is off)
    label_method             text,               -- 'rule' | 'rule_filing_date' | 'rule_far' | 'corrected' | 'no_fye' | 'no_report_date'
    label_note               text,
    note                     text,               -- from overrides.toml [[filing_notes]]
    first_seen_run           bigint NOT NULL REFERENCES runs(id),
    last_seen_run            bigint NOT NULL REFERENCES runs(id),
    not_seen_since           date,               -- the feed stopped listing it on this date; never deleted
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS filings_cik_family_idx ON filings (cik, family, fiscal_year, fiscal_period);
CREATE INDEX IF NOT EXISTS filings_filing_date_idx ON filings (filing_date);

-- The SEC's own fiscal labels per filing, from the XBRL companyfacts API (the cross-check, never the source).
CREATE TABLE IF NOT EXISTS sec_labels (
    accession_number  text PRIMARY KEY,
    fiscal_year       integer,
    fiscal_period     text,
    form              text,
    source            text NOT NULL DEFAULT 'companyfacts',
    fetched_at        timestamptz NOT NULL DEFAULT now()
);

-- One row per check per run: the morning check reads these, not the logs.
CREATE TABLE IF NOT EXISTS checks (
    id          bigserial PRIMARY KEY,
    run_id      bigint NOT NULL REFERENCES runs(id),
    name        text NOT NULL,
    severity    text NOT NULL,                   -- 'hard' | 'soft' | 'info'
    passed      boolean NOT NULL,
    summary     text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Each submissions page as fetched, kept when its content changed, with the last run that saw the same content.
CREATE TABLE IF NOT EXISTS source_snapshots (
    id            bigserial PRIMARY KEY,
    cik           bigint NOT NULL,
    page          text NOT NULL,                 -- 'recent' or the older page file name
    sha256        text NOT NULL,
    run_id        bigint NOT NULL REFERENCES runs(id),
    last_seen_run bigint,
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    body          jsonb NOT NULL,
    UNIQUE (cik, page, sha256)
);

-- One row per company, family, fiscal year and period: the original report plus its amendments. A filing is
-- reached through every registrant that listed it, so a company linked only as co-registrant still sees it.
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
