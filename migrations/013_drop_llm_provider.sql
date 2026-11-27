-- 013: drop llm.provider from settings.
-- The only provider is openai-compatible; the settings page no longer
-- offers a provider field and nothing reads this row for behaviour.
DELETE FROM settings WHERE key='llm.provider';
