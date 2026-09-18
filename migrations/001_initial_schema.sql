-- 001: Vataic initial schema (tasks 2.1-2.5). Idempotent via IF NOT EXISTS.
-- Projects / jobs / intake
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT 'default',
  created_at TEXT DEFAULT (datetime('now')));

CREATE TABLE IF NOT EXISTS projection_jobs(
  id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id),
  premise TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'macro',
  reality_mode TEXT NOT NULL DEFAULT 'grounded',
  horizon TEXT NOT NULL DEFAULT 'auto',
  outcome_count INTEGER NOT NULL DEFAULT 7,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  current_phase TEXT NOT NULL DEFAULT 'parse',
  current_step INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT, lease_expires_at TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  suspend_requested INTEGER NOT NULL DEFAULT 0,
  budget_state TEXT NOT NULL DEFAULT '{}',
  error TEXT, created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now')));

CREATE TABLE IF NOT EXISTS scenario_inputs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  premise TEXT NOT NULL, premise_type TEXT, axioms TEXT NOT NULL DEFAULT '[]',
  entities TEXT NOT NULL DEFAULT '[]', systems TEXT NOT NULL DEFAULT '[]',
  reality_mode TEXT NOT NULL DEFAULT 'grounded', horizon TEXT NOT NULL DEFAULT 'auto',
  assumptions TEXT NOT NULL DEFAULT '[]', UNIQUE(job_id));

CREATE TABLE IF NOT EXISTS factor_sets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  factors TEXT NOT NULL DEFAULT '[]', discarded TEXT NOT NULL DEFAULT '[]',
  watchlist TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now')));

CREATE TABLE IF NOT EXISTS causal_graphs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  nodes TEXT NOT NULL DEFAULT '[]', edges TEXT NOT NULL DEFAULT '[]',
  version INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT (datetime('now')));

CREATE TABLE IF NOT EXISTS world_states(
  job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  step_index INTEGER NOT NULL, time_label TEXT NOT NULL DEFAULT '',
  factor_values TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(job_id, step_index));

-- Particles / steps / llm / checkpoints
CREATE TABLE IF NOT EXISTS particles(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  parent_id TEXT REFERENCES particles(id), probability REAL NOT NULL DEFAULT 1.0,
  factor_state TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'active',
  created_step INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS particle_states(
  id INTEGER PRIMARY KEY AUTOINCREMENT, particle_id TEXT NOT NULL REFERENCES particles(id) ON DELETE CASCADE,
  step_index INTEGER NOT NULL, factor_values TEXT NOT NULL DEFAULT '{}',
  events TEXT NOT NULL DEFAULT '[]', UNIQUE(particle_id, step_index));

CREATE TABLE IF NOT EXISTS projection_steps(
  job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  step_index INTEGER NOT NULL, time_label TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '', turning_points TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY(job_id, step_index));

CREATE TABLE IF NOT EXISTS llm_calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  cache_key TEXT NOT NULL, phase TEXT NOT NULL DEFAULT '',
  step_index INTEGER NOT NULL DEFAULT 0, particle_id TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '', request TEXT NOT NULL DEFAULT '{}',
  response TEXT NOT NULL DEFAULT '{}', cache_hit INTEGER NOT NULL DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')), UNIQUE(cache_key));

CREATE TABLE IF NOT EXISTS checkpoints(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  phase TEXT NOT NULL DEFAULT 'projection_loop', step_index INTEGER NOT NULL DEFAULT 0,
  payload TEXT NOT NULL DEFAULT '{}', budget TEXT NOT NULL DEFAULT '{}',
  immutable INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT (datetime('now')));

-- Outcomes / artifacts / events
CREATE TABLE IF NOT EXISTS outcomes(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  name TEXT NOT NULL DEFAULT '', probability REAL NOT NULL DEFAULT 0,
  band TEXT NOT NULL DEFAULT 'plausible', summary TEXT NOT NULL DEFAULT '',
  factor_states TEXT NOT NULL DEFAULT '{}', causal_path TEXT NOT NULL DEFAULT '[]',
  indicators TEXT NOT NULL DEFAULT '[]', assumptions TEXT NOT NULL DEFAULT '[]',
  risks TEXT NOT NULL DEFAULT '[]', explanation TEXT NOT NULL DEFAULT '',
  representative_particle TEXT NOT NULL DEFAULT '', enrichment TEXT NOT NULL DEFAULT '{}');

CREATE TABLE IF NOT EXISTS outcome_particle_memberships(
  outcome_id TEXT NOT NULL REFERENCES outcomes(id) ON DELETE CASCADE,
  particle_id TEXT NOT NULL REFERENCES particles(id) ON DELETE CASCADE,
  weight REAL NOT NULL DEFAULT 0, PRIMARY KEY(outcome_id, particle_id));

CREATE TABLE IF NOT EXISTS artifacts(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
  created_at TEXT DEFAULT (datetime('now')));

CREATE TABLE IF NOT EXISTS job_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  from_state TEXT NOT NULL DEFAULT '', to_state TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '', created_at TEXT DEFAULT (datetime('now')));

-- Personal scope
CREATE TABLE IF NOT EXISTS subjects(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  privacy TEXT NOT NULL DEFAULT 'private', created_at TEXT DEFAULT (datetime('now')));

CREATE TABLE IF NOT EXISTS subject_profiles(
  subject_id TEXT PRIMARY KEY REFERENCES subjects(id) ON DELETE CASCADE,
  facts TEXT NOT NULL DEFAULT '{}', goals TEXT NOT NULL DEFAULT '[]',
  constraints_s TEXT NOT NULL DEFAULT '[]', values_s TEXT NOT NULL DEFAULT '[]',
  risk_tolerance TEXT NOT NULL DEFAULT 'moderate', agency TEXT NOT NULL DEFAULT '{}',
  support_system TEXT NOT NULL DEFAULT '{}', unknowns TEXT NOT NULL DEFAULT '[]',
  assumptions TEXT NOT NULL DEFAULT '[]');

CREATE TABLE IF NOT EXISTS personal_projection_settings(
  job_id TEXT PRIMARY KEY REFERENCES projection_jobs(id) ON DELETE CASCADE,
  stop_loss REAL, decision_window TEXT NOT NULL DEFAULT '',
  settings TEXT NOT NULL DEFAULT '{}');

CREATE TABLE IF NOT EXISTS personal_factor_states(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  particle_id TEXT NOT NULL DEFAULT '', step_index INTEGER NOT NULL DEFAULT 0,
  factors TEXT NOT NULL DEFAULT '{}');

CREATE TABLE IF NOT EXISTS personal_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  particle_id TEXT NOT NULL DEFAULT '', step_index INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}');

CREATE TABLE IF NOT EXISTS safety_flags(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES projection_jobs(id) ON DELETE CASCADE,
  flagged INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
  resources TEXT NOT NULL DEFAULT '', created_at TEXT DEFAULT (datetime('now')));

-- Settings / leases / idempotency
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS leases(
  job_id TEXT PRIMARY KEY REFERENCES projection_jobs(id) ON DELETE CASCADE,
  owner TEXT NOT NULL DEFAULT '', expires_at TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS idempotency_records(
  key TEXT PRIMARY KEY, result TEXT NOT NULL DEFAULT '{}',
  created_at TEXT DEFAULT (datetime('now')));
