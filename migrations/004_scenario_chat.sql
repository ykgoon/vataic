-- 004: scenario chat messages (openspec 2026-11-01-scenarios-ui-overhaul).
BEGIN;
CREATE TABLE IF NOT EXISTS scenario_chat_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scenario_id TEXT NOT NULL REFERENCES projection_scenarios(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'user',
  content TEXT NOT NULL DEFAULT '',
  created_at TEXT DEFAULT (datetime('now')));
CREATE INDEX IF NOT EXISTS idx_chat_scenario ON scenario_chat_messages(scenario_id);
COMMIT;
