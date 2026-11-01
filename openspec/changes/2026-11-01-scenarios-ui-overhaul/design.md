# Design

## Run readiness gate (item 1)

New module `app/llm/prepared.py`:

- `provider_status(db_path=None) -> {"ok": bool, "detail": str, "model": str}` —
  effective-config probe of `GET {base_url}/models` with the configured key. `ok=False`
  on connection errors/timeouts, or when a model list IS available and neither the
  primary (parse) nor the strong (chat/explanation) model is offered. A reachable
  provider with no listable models is treated as usable (OpenAI-compatible proxies
  commonly 404 on /models).
- `require_provider(job_id)` — called at the top of `act_parse` (first pipeline
  activity): when `provider_status` is not ok AND `VATAIC_ALLOW_HEURISTIC` is unset,
  raise `RuntimeError("LLM provider unavailable: {detail}")`. The run_sync error path
  already records status FAILED with the message; the UI already offers Retry, which
  re-queues through the existing `FAILED → QUEUED` transition.
- Submitting while the provider is down still works (QUEUED); the failure lands at the
  next worker poll, which the dashboard polling already surfaces within seconds.
- Chat reuses `provider_status`: on first message with the provider down, the panel
  shows an error bubble and nothing is persisted.

Tests: `tests/conftest.py` sets `VATAIC_ALLOW_HEURISTIC=1` so offline suites keep
exercising the full pipeline; a new test asserts the gate fails a scenario without the
hatch and passes with a fake provider (same `requests.post` monkeypatch pattern as the
usage test).

## Scenario deletion (item 2)

`app/scenarios/repos.py: delete_scenario(jid, db_path=None)` opens `BEGIN IMMEDIATE`
and deletes from every child table (scenario_inputs, factor_sets, causal_graphs,
world_states, particles, particle_states, projection_steps, llm_calls, checkpoints,
outcomes, outcome_particle_memberships, artifacts, scenario_events, subjects,
subject_profiles, personal_projection_settings, personal_factor_states, personal_events,
safety_flags, leases, scenario_chat_messages, idempotency rows for the scenario where
identifiable) and finally the scenario row. Route `POST /scenarios/<jid>/delete` also
`shutil.rmtree(artifact_dir/<jid>, ignore_errors=True)` and redirects to the dashboard.
Deletion is refused for non-terminal states (cancel first) to avoid racing the worker.
UI: a small `hx-post` + `hx-confirm` button on dashboard rows and the detail page.

## Compose reload without rebuild (item 4)

```yaml
volumes:
  - ./data:/data
  - ./app:/srv/app
  - ./worker:/srv/worker
  - ./migrations:/srv/migrations
```
on both services. Gunicorn resolves templates per request; `docker compose restart web
worker` reloads every Python + template change (fresh interpreter). Images stay for
dependency changes only. `.dockerignore` (data/, .git, __pycache__, .venv, screens/) and
`.gitignore` (data/) added. Podman quadlet units keep the baked-image contract (prod).

## Rename sweep (item 5)

One mechanical pass, committed alone, tests green before features land on the new
vocabulary:

1. `git mv app/jobs app/scenarios` (+ file renames `repositories.py`→`repos.py`,
   `states.py`→`state.py`); `git mv app/routes/jobs.py app/routes/scenarios.py`;
   `git mv app/templates/jobs app/templates/scenarios`;
   `git mv tests/test_vataic.py tests/test_scenarios.py`.
2. Targeted seds first (before the word-boundary pass can clobber module paths):
   `app.jobs.repositories`→`app.scenarios.repos`, `app.jobs.states`→`app.scenarios.state`,
   `ProjectionJobWorkflow`→`ProjectionScenarioWorkflow`.
3. Word-boundary sed over all tracked text files:
   `[Jj]obs\b`→`scenarios`, `[Jj]ob\b`→`scenario`, then `Jobs`→`Scenarios`,
   `Job`→`Scenario`. SQL table names (`projection_jobs`, `job_events`), route URLs, CSS
   classes, and JS ride the same sweep; migrations and code rename consistently.
4. README + openspec/specs prose updates ride the same sweep; requirement headers with
   "Job" are renamed in this change's deltas (archive rolls the canonical text).
5. User-facing "Step"→"Cycle" wording only in templates + README; internal vocabulary
   (budget key `steps`, `step_index`, `run_step`) unchanged.

## Scenario chat (item 6)

- Migration `003_scenario_chat.sql`: `scenario_chat_messages(scenario_id, role, content,
  created_at)` + index; `ON DELETE CASCADE` on the scenario FK.
- Blueprint `app/routes/chat.py`: `GET/POST /scenarios/<jid>/chat`,
  `POST /scenarios/<jid>/chat/clear`. The detail template includes the panel; send is
  `hx-post` targeting the panel wrapper (htmx swap, no reload).
- Context per message: card (premise/type/horizon/reality mode/axioms/assumptions),
  selected factors (human names, current values), causal edges, outcomes
  (name/probability/band/summary), last 12 cycle summaries — JSON-truncated to ~6k
  chars. System prompt: ground every answer in stored data; plain language; say plainly
  when something is not recorded; never invent factors/outcomes.
- Storage: both roles persisted with timestamps; re-rendered from DB on GET.
- Model: `chat` phase routed to the strong role (`ROLE_PHASES["strong"] |= {"chat"}`).
- Failure: provider error → 200 with an error bubble in the panel; message not stored.
