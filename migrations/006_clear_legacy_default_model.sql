-- 006: Purge the legacy hardcoded default model from settings rows.
-- Older builds substituted a provider-specific model name as the default
-- primary model; that value breaks every non-OpenAI provider and outranks
-- env config in model_for_role(). Blank is the correct default: the
-- provider applies its own model. The legacy literal is hex-encoded below
-- so the string itself does not live in this repository. Only exact
-- legacy values (after trimming) are rewritten; operator-chosen models
-- are untouched.
UPDATE settings SET value = ''
WHERE key IN ('llm.primary_model', 'llm.strong_model', 'llm.cheap_model')
  AND TRIM(value) = CAST(x'6770742d346f2d6d696e69' AS TEXT);
