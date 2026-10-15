# AGENTS.md — vataic

Flask + htmx scenario lab. SQLite (WAL) is system of record, `data/artifacts/` holds blobs. In-process `ProjectionJobWorkflow` (`worker/workflows.py`) orchestrates `app/steps/` pipeline with per-step DB checkpoints.

## Commands (use `uv run`, never bare `python`)

```sh
cp .env.example .env
uv sync
uv run python -m app.migrate   # idempotent; also auto-runs on Flask startup
uv run pytest                  # full suite, sqlite-backed, no services needed
uv run pytest tests/test_vataic.py -k <name>  # single test
uv run flask --app app run     # dev server; prod: gunicorn 'app:create_app()'
uv run python scripts/smoke_test.py  # end-to-end checkpoint/resume check
docker compose up              # web + worker + Temporal dev server (sqlite mode)
```

No lint/typecheck/CI config in repo. `pyproject.toml` only defines pytest (`testpaths=["tests"]`).

## Config / env

- `.env.example` documents every variable; `app/config.py` (`load_dotenv` + `Settings`) is source of truth. Relative `DATABASE_PATH`/`ARTIFACT_DIR` resolve against cwd (`app/db.py:resolve_db_path`), not repo root.
- `LLM_API_KEY` env-only, never stored in DB (`llm_calls` stores request/response + `cache_key`, no key).
- Only provider is `openai-compatible` (covers OpenAI, Ollama, vLLM, proxies). Role routing: `model_for_phase()` in `app/llm/client.py` (strong: parse/causal/step/explanation; cheap: epoch/cluster); `LLM_*_MODEL` empty falls back to `LLM_PRIMARY_MODEL`.

## Architecture notes agents miss

- Entry: `app/__init__.py:create_app()` lazily registers `app.routes.{jobs,results,personal,settings}` blueprints (try/except, missing blueprint = silently skipped). `/healthz` returns `{"ok": True}`.
- Pipeline order: `parse_scenario` → `select_factors` → `build_causal_graph` (+`critic_revise`) → `init_world_state` → `projection_loop` (`act_loop_step`, one step per activity, continue-as-new pattern) → `cluster_outcomes` (`synthesize`) → `generate_explanations` → `finalize_report` (`write_artifact`). See `worker/workflows.py:PHASES`.
- Durability rules: Temporal activities are idempotent via `idempotency_records` (`idem_key`/`idem_get`/`idem_put`, `INSERT OR IGNORE`) and `llm_calls.cache_key` UNIQUE. DB write happens before activity completion. Resume reads `latest_checkpoint()`, never restarts at step 0. Recovery: `scan_recoverable()` finds `RUNNING/RECOVERING/CHECKPOINTING` jobs with expired `lease_expires_at` and moves them to `RECOVERING`.
- State machine `app/jobs/states.py:ALLOWED` strictly enforced (`transition()` raises `InvalidTransition`); e.g. `QUEUED→COMPLETED` illegal, `FAILED→QUEUED` is the retry path.
- Storage: large payloads go to disk via `app/store.py:write_artifact()` (`<ARTIFACT_DIR>/<job_id>/<id>.<kind>.json`); DB `artifacts` table holds path only. Checkpoints store JSON `payload`+`budget` with `immutable=1`.
- Migrations: `migrations/*.sql` applied in sorted order via `app/migrate.py:apply_pending()`, tracked in `schema_migrations`. Schema is single file `001_initial_schema.sql`; `app/seed.py:seed()` fills `settings` table, both idempotent.
- Tests (`tests/test_vataic.py`) isolate DB via `monkeypatch DATABASE_PATH=/tmp/vataic-test.db` + `importlib.reload(app.config)` + `apply_pending(DB); seed(DB)` in autouse fixture. Follow that pattern for repro scripts; pass `db_path=DB` explicitly to repository functions.
