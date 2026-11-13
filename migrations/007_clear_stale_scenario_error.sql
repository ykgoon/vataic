-- 007: Retire error messages that no longer belong to the scenario's state.
-- projection_scenarios.error is shown next to the status, so it must describe
-- the *current* run only. Older builds never cleared it on retry: a scenario
-- that failed and was later retried to COMPLETED (or is running again) still
-- carried the previous attempt's message. Keep the text for FAILED rows, drop
-- it everywhere else. Idempotent: a second run matches zero rows.
UPDATE projection_scenarios SET error = NULL
WHERE error IS NOT NULL AND error <> '' AND status <> 'FAILED';
