# Scenario Terminology, Run Readiness, and Conversational Deep-Dive — Tasks

## 1. Rename job → scenario

- [ ] 1.1 OpenSpec change committed (proposal, design, tasks, delta specs)
- [ ] 1.2 Mechanical sweep: dirs/files renamed, seds applied, README + specs text updated
- [ ] 1.3 Full test suite green after the rename; UI shows scenario terminology

## 2. Compose reload without rebuild

- [ ] 2.1 Bind mounts (app/, worker/, migrations/) + `.dockerignore` + `.gitignore`; compose config valid
- [ ] 2.2 Deploy once with `docker compose up -d`; verify `docker compose restart` reloads a logic change live

## 3. Navigation

- [ ] 3.1 Remove Personal link from top nav; `/personal` routes remain reachable

## 4. Run readiness gate + retry

- [ ] 4.1 `app/llm/prepared.py` (provider_status, require_provider); gate in act_parse; `VATAIC_ALLOW_HEURISTIC` escape hatch documented in `.env.example`
- [ ] 4.2 UI surfaces the FAILED state with readiness error + existing retry control (dashboard row + detail)
- [ ] 4.3 Tests: gate fails a scenario offline (no hatch), passes with a fake provider

## 5. Scenario deletion

- [ ] 5.1 `delete_scenario()` transactional delete + artifact dir removal; route + confirm buttons (dashboard row, detail page)
- [ ] 5.2 Tests: delete removes scenario, children, artifacts; non-terminal states blocked

## 6. Scenario chat

- [ ] 6.1 Migration `003_scenario_chat.sql`; `app/routes/chat.py` (send/list/clear); context builder; `chat` → strong role
- [ ] 6.2 Detail-page chat panel (htmx send, persisted history, clear control) + styles
- [ ] 6.3 Tests: POST stores both messages and returns the panel; GET renders history; provider failure shows error bubble and stores nothing

## 7. Verification

- [ ] 7.1 `uv run pytest` full suite green on devbox
- [ ] 7.2 `openspec validate --strict` clean for the change
- [ ] 7.3 Live smoke on devbox compose: scenario fails fast with provider down (and retries), chat round-trip works, delete works, nav updated
- [ ] 7.4 Screenshots stored in `vataic/screens/`
