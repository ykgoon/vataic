-- 012: settings consolidation (no-env config).
-- The settings table is now the primary config source, edited on /settings.
-- Legacy flat keys are renamed to match the editable key set in
-- app/settings_store.py; legacy budget.* LLM caps are dropped (runs are
-- never call/token capped); any other stray rows are removed. Keyed UPDATE
-- (no OR REPLACE): the PK is `key`, so a rename must not collide.
BEGIN;
UPDATE settings SET key='branch.prob_threshold'
 WHERE key='branch_prob_threshold'
   AND NOT EXISTS (SELECT 1 FROM settings WHERE key='branch.prob_threshold');
UPDATE settings SET key='tail_risk.impact_threshold'
 WHERE key='tail_risk_impact_threshold'
   AND NOT EXISTS (SELECT 1 FROM settings WHERE key='tail_risk.impact_threshold');
DELETE FROM settings WHERE key IN ('budget.max_llm_calls','budget.max_tokens');
DELETE FROM settings WHERE key NOT IN (
  'llm.provider','llm.base_url','llm.primary_model','llm.strong_model','llm.cheap_model',
  'llm.timeout_s','llm.max_attempts',
  'budget.max_steps','budget.max_branches','budget.max_runtime_s',
  'beam.width','beam.reserved_divergent','merge.distance_threshold',
  'branch.prob_threshold','tail_risk.impact_threshold',
  'factors.max_macro','factors.max_personal',
  'step.min_days','step.max_days','epoch.interval_steps','context.token_budget',
  'version');
COMMIT;
