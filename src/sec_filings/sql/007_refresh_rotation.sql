-- After review pass 4: the SEC-label refresh queue is served oldest-attempt first so no filer can starve behind
-- the per-run cap; the attempt time is recorded per filer.
ALTER TABLE filers ADD COLUMN IF NOT EXISTS sec_labels_attempted_at timestamptz;
