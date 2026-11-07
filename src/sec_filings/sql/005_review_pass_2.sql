-- After review pass 2: an outage gap is persistent state per filer until a history read repairs it, so the alarm
-- cannot fade because a later run merely reached its checks.
ALTER TABLE filers ADD COLUMN IF NOT EXISTS coverage_gap_since date;
