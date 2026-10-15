# Vataic — world projector
Async scenario laboratory: premise → factors → causal graph → particle projection → clustered outcomes.

## Quickstart
```sh
cp .env.example .env
uv sync
uv run python -m app.migrate
uv run pytest
uv run flask --app app run  # or gunicorn 'app:create_app()'
docker compose up  # web + worker (in-process projection worker, no Temporal)
uv run python scripts/smoke_test.py
```

## Architecture
Flask + htmx, SQLite (WAL) system of record, `data/artifacts/` for blobs,
in-process projection worker (`ProjectionJobWorkflow`) with per-step DB checkpoints,
OpenAI-compatible `LLMClient` with cache + retry + role routing,
numpy/sklearn clustering, SVG/htmx result explorer.

## Config
See `.env.example` — every variable documented. API key env-only, never in DB.

## Limitations
Single-node, no auth, in-process worker is a single process (not production-grade),
projections are scenario explorations not predictions, crisis premises halt with resources.
