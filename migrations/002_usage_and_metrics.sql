-- 002: per-call token usage columns for the LLM gateway, plus lookup indexes.
-- Applied once via schema_migrations tracking (see app/migrate.py).
ALTER TABLE llm_calls ADD COLUMN prompt_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE llm_calls ADD COLUMN completion_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE llm_calls ADD COLUMN total_tokens INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_llm_calls_job ON llm_calls(job_id);
CREATE INDEX IF NOT EXISTS idx_particles_job ON particles(job_id);
