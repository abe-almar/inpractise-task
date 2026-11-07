-- After review pass 3: coverage is tracked per filer as a watermark (the date through which its filings are known
-- to be complete), advanced only when a fetched page overlaps it or when history pages are read. A run that merely
-- reached its checks cannot bridge a gap, and a history read always can.
ALTER TABLE filers ADD COLUMN IF NOT EXISTS coverage_through date;
