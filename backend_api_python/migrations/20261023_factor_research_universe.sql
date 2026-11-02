ALTER TABLE qd_factor_research_runs ALTER COLUMN source_id DROP NOT NULL;
ALTER TABLE qd_factor_research_runs ADD COLUMN IF NOT EXISTS universe_id INTEGER;
ALTER TABLE qd_factor_research_runs ADD COLUMN IF NOT EXISTS universe_name VARCHAR(255) DEFAULT '';
ALTER TABLE qd_factor_research_runs ADD COLUMN IF NOT EXISTS research_mode VARCHAR(20) NOT NULL DEFAULT 'strategy';

CREATE INDEX IF NOT EXISTS idx_factor_research_runs_user_universe_recent
  ON qd_factor_research_runs(user_id, universe_id, id DESC);
