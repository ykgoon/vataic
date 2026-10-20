## 1. Project packaging

- [x] 1.1 Add `[build-system]` (`requires = ["setuptools>=68"]`, `build-backend = "setuptools.build_meta"`) to `pyproject.toml`
- [x] 1.2 Run `uv lock --check` (and `uv sync`) to confirm the lockfile is unchanged/consistent

## 2. Dockerfile.web

- [x] 2.1 Switch base image to `ghcr.io/astral-sh/uv:python3.11-bookworm-slim` (pin concrete uv tag)
- [x] 2.2 `WORKDIR /srv`; `COPY pyproject.toml uv.lock ./`; `RUN uv sync --frozen --no-dev --no-install-project`
- [x] 2.3 Add `ENV PATH="/srv/.venv/bin:$PATH"`; `COPY app ./app` and `COPY migrations ./migrations`
- [x] 2.4 Keep `ENV DATABASE_PATH=/data/vataic.db ARTIFACT_DIR=/data/artifacts`
- [x] 2.5 Set CMD to `sh -c "python -m app.migrate; exec gunicorn 'app:create_app()' -b 0.0.0.0:5000"` (no separate gunicorn install line)

## 3. Dockerfile.worker

- [x] 3.1 Switch base image to `ghcr.io/astral-sh/uv:python3.11-bookworm-slim` (pin concrete uv tag)
- [x] 3.2 `WORKDIR /srv`; `COPY pyproject.toml uv.lock ./`; `RUN uv sync --frozen --no-dev --no-install-project`
- [x] 3.3 Add `ENV PATH="/srv/.venv/bin:$PATH"`; `COPY app ./app`, `COPY worker ./worker`, `COPY migrations ./migrations`
- [x] 3.4 Keep `ENV DATABASE_PATH=/data/vataic.db ARTIFACT_DIR=/data/artifacts`
- [x] 3.5 Set CMD to `python -m worker.main`

## 4. Verify builds

- [x] 4.1 `docker compose build` succeeds for both web and worker
- [x] 4.2 `docker compose up` → `curl localhost:5000/healthz` returns `{"ok": true}`
- [x] 4.3 Submit a scenario at `/jobs/new` and confirm the worker processes it (job reaches COMPLETED)
- [x] 4.4 Confirm source-only rebuild reuses the dependency layer (fast rebuild)

## 5. Docs

- [x] 5.1 Update `README.md` Quickstart docker line to note uv-based image build
- [x] 5.2 Update `deploy/podman/README.md` build instructions to reflect uv-based images
