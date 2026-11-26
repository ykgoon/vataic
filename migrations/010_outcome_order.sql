-- 010_outcome_order.sql
-- Adds the measured order-of-effect record to outcomes, and the causal hops a
-- particle took to particle_states.
--
-- 008 declared outcome_order: 1st = direct consequence of the decision, 2nd =
-- downstream of that, 3rd = further downstream. Nothing computed it, so every
-- row defaulted to 1 and the UI grew an "O1" badge that was always the same.
--
-- The order is now measured: a development names factors directly (hop 0) and
-- app.steps.causality.propagate pushes those deltas along causal_graphs edges,
-- so a factor that moved only because its driver moved is at hop >= 1. The
-- outcome order is the shallowest hop among the factors it actually leans on.
ALTER TABLE outcomes ADD COLUMN causal_hops TEXT NOT NULL DEFAULT '';

-- Causal hops taken within one projection step, factor -> hop, for the
-- trajectory that produced this state. Empty for runs that predate the causal
-- loop (their hop record does not exist and must not be invented).
ALTER TABLE particle_states ADD COLUMN causal_hops TEXT NOT NULL DEFAULT '';

-- Whether the causal graph was loaded into that step's prompt. Distinguishes a
-- genuinely causal run from one where the projection ran blind.
ALTER TABLE particle_states ADD COLUMN causal_source TEXT NOT NULL DEFAULT '';
