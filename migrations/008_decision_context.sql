-- 008_decision_context.sql
-- Adds decision_context to projection_scenarios and scenario_inputs,
-- and order to outcomes.

-- decision_context on projection_scenarios: the user's decision-making
-- context (what they're deciding, constraints, timeframe, what would make
-- the decision "good"). Captured at intake, used to ground the LLM's
-- framing throughout the pipeline.
ALTER TABLE projection_scenarios ADD COLUMN decision_context TEXT NOT NULL DEFAULT '';

-- decision_context on scenario_inputs: same field, denormalized into the
-- inputs table so the parse step and LLM prompts can read it directly.
ALTER TABLE scenario_inputs ADD COLUMN decision_context TEXT NOT NULL DEFAULT '';

-- order on outcomes: which order of effect this outcome represents.
--   1st order: direct, immediate consequence of the decision
--   2nd order: downstream effect caused by the 1st-order consequence
--   3rd order: second-order effect (further downstream)
-- LLM-assigned during synthesis; defaults to 1.
ALTER TABLE outcomes ADD COLUMN "order" INTEGER NOT NULL DEFAULT 1;
