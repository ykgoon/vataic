# Scenario Terminology, Run Readiness, and Conversational Deep-Dive

## Why

Six product issues, one change:

1. **Heuristic prose is useless.** When the LLM provider is unreachable, the pipeline
   silently degrades to deterministic template prose ("Regulatory Burden ends well above
   baseline…"). A projection completed this way reads like analysis but is not one — it is
   factor arithmetic with filler. Only LLM narration is useful; a no-LLM run should not
   pretend to succeed.
2. **Old runs accumulate.** There is no way to delete a scenario from the UI; the dashboard
   grows unboundedly.
3. **The "Personal" nav link leads to a peripheral page.** Personal scenarios are created
   from the submission form and listed on the dashboard; a top-level nav slot
   misrepresents the page's weight. The route stays.
4. **Every logic change needs an image rebuild.** Both Dockerfiles bake `app/` and
   `worker/` into the image, so a one-line template edit requires
   `docker compose build` before `up`. On a dev box, bind mounts + `docker compose
   restart` should be the normal reload path.
5. **"Job" is the wrong word.** The artifact under discussion — a premise, parsed factors,
   explored timelines, and outcomes — is a *scenario*. The code, UI, routes, templates,
   tests, docs, and specs say "job" hundreds of times.
6. **No way to interrogate a finished projection.** The futures view is static prose; the
   natural follow-up questions ("why is this outcome 18%?", "what if the licensing bill
   fails?") currently require reading the causal graph by hand.

## What Changes

- **Run readiness gate**: creating a scenario stays possible offline, but worker pickup
  validates the configured LLM provider (reachability + configured models when listable)
  and transitions the scenario to FAILED with a clear, retryable error when the provider
  is unusable. Heuristic fallbacks remain in the code but are reachable only via the
  documented `VATAIC_ALLOW_HEURISTIC` escape hatch (tests, emergency).
- **Scenario deletion**: `POST /scenarios/<id>/delete` removes the scenario row, all child
  rows, and on-disk artifacts, with a confirm dialog; terminal-state scenarios only.
- **Navigation**: the "Personal" link is removed from the top nav; `/personal` routes stay.
- **Reload without rebuild**: compose bind-mounts `app/`, `worker/`, `migrations/` into
  both containers; `docker compose restart` reloads code and template changes. Images are
  rebuilt only when dependencies (uv.lock) change. `.dockerignore` added.
- **Rename job → scenario** across backend, worker, UI, routes, templates, CSS/JS, tests,
  README, and openspec texts. `projection_jobs`→`projection_scenarios`,
  `job_events`→`scenario_events`, `/jobs/…`→`/scenarios/…`, `ProjectionScenarioWorkflow`,
  `app/scenarios/` (repositories.py→repos.py, states.py→state.py).
- **Scenario chat**: a conversational panel on the scenario detail page. Each turn is a
  cached, recorded LLM call grounded in the stored scenario context (premise card,
  selected factors, causal edges, outcomes with probabilities, recent cycle summaries),
  persisted in `scenario_chat_messages` (new migration), re-rendered on reload.

## Capabilities

### New Capabilities
- `scenario-chat`: conversational deep-dive over one stored scenario.

### Modified Capabilities
- `durable-jobs`: run-readiness validation at pickup; deletion control; scenario terminology.
- `web-ui`: submission/dashboard/detail terminology; nav slimming; delete affordances;
  chat panel entry point.
- `llm-gateway`: chat role routing; provider preflight helper.
- `container-build`: source bind mounts; restart-reload contract.
- `scenario-intake`, `memory-compression`, `personal-projection`: terminology-only deltas.

## Impact

- **Specs**: deltas under `openspec/changes/2026-11-01-scenarios-ui-overhaul/specs/`.
- **DB**: one additive migration (`003_scenario_chat.sql`). The projection table's
  self-referencing FK is NOT migrated (SQLite FK rebuild risk); deletion removes child
  rows explicitly inside one transaction.
- **Deploy**: compose changes require one `docker compose up -d` (recreate) after this
  change; afterwards `docker compose restart` suffices for logic edits.
- **Non-goals**: renaming the openspec capability folder `durable-jobs` (noted for the
  archive pass); chat streaming; multi-scenario comparison; auth.
