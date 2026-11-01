-- 005: chat reply rows carry a failure reason (openspec 2026-11-01-scenarios-ui-overhaul).
BEGIN;
ALTER TABLE scenario_chat_messages ADD COLUMN error TEXT NOT NULL DEFAULT '';
COMMIT;
