-- 003: rename job -> scenario (openspec 2026-11-01-scenarios-ui-overhaul).
-- Code, templates, and specs now say "scenario"; the SQL schema follows.
-- Wrapped in an explicit transaction: either every rename applies or none
-- (a failure rolls back and the migration can be re-run after a fix).
-- SQLite ALTER TABLE RENAME updates index definitions and FK clauses that
-- reference the renamed objects, so no table rebuilds are needed.

BEGIN;

ALTER TABLE projection_jobs RENAME TO projection_scenarios;
ALTER TABLE job_events RENAME TO scenario_events;

-- Child tables: rename the FK column job_id -> scenario_id.
ALTER TABLE scenario_inputs RENAME COLUMN job_id TO scenario_id;
ALTER TABLE factor_sets RENAME COLUMN job_id TO scenario_id;
ALTER TABLE causal_graphs RENAME COLUMN job_id TO scenario_id;
ALTER TABLE world_states RENAME COLUMN job_id TO scenario_id;
ALTER TABLE particles RENAME COLUMN job_id TO scenario_id;
ALTER TABLE projection_steps RENAME COLUMN job_id TO scenario_id;
ALTER TABLE llm_calls RENAME COLUMN job_id TO scenario_id;
ALTER TABLE checkpoints RENAME COLUMN job_id TO scenario_id;
ALTER TABLE outcomes RENAME COLUMN job_id TO scenario_id;
ALTER TABLE artifacts RENAME COLUMN job_id TO scenario_id;
ALTER TABLE scenario_events RENAME COLUMN job_id TO scenario_id;
ALTER TABLE subjects RENAME COLUMN job_id TO scenario_id;
ALTER TABLE personal_projection_settings RENAME COLUMN job_id TO scenario_id;
ALTER TABLE personal_factor_states RENAME COLUMN job_id TO scenario_id;
ALTER TABLE personal_events RENAME COLUMN job_id TO scenario_id;
ALTER TABLE safety_flags RENAME COLUMN job_id TO scenario_id;
ALTER TABLE leases RENAME COLUMN job_id TO scenario_id;

-- Indexes: SQLite cannot ALTER an index; drop and recreate with new names.
-- (Their column definitions already follow the column rename above.)
DROP INDEX IF EXISTS idx_llm_calls_job;
CREATE INDEX IF NOT EXISTS idx_llm_calls_scenario ON llm_calls(scenario_id);
DROP INDEX IF EXISTS idx_particles_job;
CREATE INDEX IF NOT EXISTS idx_particles_scenario ON particles(scenario_id);

COMMIT;
