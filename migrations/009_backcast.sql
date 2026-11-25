-- 009_backcast.sql
-- Adds backcast column to projection_scenarios (Phase 3: LLM-driven backcast).
-- Stores the JSON result of the backcast-to-decisions LLM phase:
-- a mapping of outcome name -> list of decisions that would lead to it.
ALTER TABLE projection_scenarios ADD COLUMN backcast TEXT DEFAULT '';
