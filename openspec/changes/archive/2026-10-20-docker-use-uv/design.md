## Context

Local dev already standardizes on `uv`:
- `pyproject.toml` + `uv.lock` (hash-pinned, `requires-python = ">=3.11"`, resolution markers for 3.11 vs 3.12) is the single dependency truth.
- `AGENTS.md` / `README.md` mandate `uv sync` / `uv run`; the repo venv is `uv`-managed CPython 3.11.13.

The container pipeline remained on pip: both `Dockerfile.web` and `Dockerfile.worker`
`COPY pyproject.toml` then `pip install --no-cache-dir -e .` (plus a redundant
`pip install gunicorn`). Problems: editable install of a tree with **no package dir yet**
(no `app/` in that layer), **no `[build-system]`** declared, and pip resolving deps
independently from `uv.lock`. Result: fragile/failing `docker build` (and the `podman build`
path documented in `deploy/podman/README.md`, which targets a separate machine).

## Goals / Non-Goals

**Goals:**
- Images install dependencies **from `uv.lock`** via `uv sync --frozen`, byte-for-byte matching local dev.
- Reproducible, cache-friendly layers: dependencies cached independently of app source.
- Same runtime contract as today: env vars, database/artifact mount points, entrypoints, `docker compose` wiring, `vataic-web`/`vataic-worker` image tags.
- Properly buildable package metadata (`[build-system]` in `pyproject.toml`).
- Docs updated so `docker compose up` and the podman quadlet flow both use the new images.

**Non-Goals:**
- No change to application code, DB schema, or compose service topology.
- No container hardening (non-root user, distroless, multi-stage security lockdown) — future work.
- No change to how local (non-container) dev installs packages.
- No change to the podman quadlet unit files (`*.container` unchanged).

## Decisions

### D1: `uv` official image instead of `python:3.11-slim` + pip
Use `ghcr.io/astral-sh/uv:python3.11-bookworm-slim` as the base for both images
(bundled uv + CPython 3.11; matches local `3.11.13`, and the lockfile's
`<3.12` marker branch).
**Alternative considered:** `python:3.11-slim` + `COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/` then `uv sync --frozen` into `/usr/local`. Slightly smaller final image; rejected because it splits the runtime manager (system Python managed by pip installs vs uv venv) and adds an extra pinned move per registry.
**Alternative considered:** `uv export --frozen --no-dev --no-hashes -o requirements.txt` + `pip install -r requirements.txt`. Keeps pip as installer but reintroduces a pip dependency and an export step per build; rejected — "keep the lockfile the single truth" is the whole point.

### D2: `uv sync --frozen --no-dev --no-install-project` in its own layer
```dockerfile
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
```
- `--frozen`: refuse to re-resolve; install exactly what `uv.lock` pins.
- `--no-dev`: images never carry the `dev` group (pytest).
- `--no-install-project`: deps resolve before `app/` exists, and any change to app source does **not** invalidate the dependency layer — this is the same caching trick pip users get from `requirements.txt` first.
- venv lands at `/srv/.venv` (uv default) → `ENV PATH="/srv/.venv/bin:$PATH"` so `python`, `gunicorn` resolve from the project venv.
**Alternative considered:** `COPY` app source first then plain `uv sync --frozen --no-dev` (installs project too). Simpler, but every app edit re-runs the entire sync layer. Rejected on rebuild-speed grounds.

### D3: Add `[build-system]` to `pyproject.toml`
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
```
The project is PEP 517-declarable today; this makes any build backend (uv's own build, pip if ever reintroduced) well-formed. With `--no-install-project`, no package discovery config is needed at build time. `uv lock` still governs installed versions.

### D4: shared pattern, web vs worker differ only in copied dirs + CMD
Both Dockerfiles:
| File | Extra COPYs | CMD |
|---|---|---|
| `Dockerfile.web` | `app/`, `migrations/` | `sh -c "python -m app.migrate; exec gunicorn 'app:create_app()' -b 0.0.0.0:5000"` |
| `Dockerfile.worker` | `app/`, `worker/`, `migrations/` | `python -m worker.main` |

`gunicorn` needs no separate install line — it is already a `pyproject.toml` dependency.

### D5: env/wiring unchanged
`docker-compose.yml` keeps `env_file: [.env]`, `DATABASE_PATH=/data/vataic.db`,
`ARTIFACT_DIR=/data/artifacts`, `./data:/data` volume, and the web→worker
`depends_on`. Podman `deploy/podman/*.container` untouched; `deploy/podman/README.md`
build commands (`podman build -f Dockerfile.web .`) still valid — just built with uv now.

## Risks / Trade-offs

- **Base image tag drift** (`ghcr.io/astral-sh/uv:python3.11-...` floats to newer uv) → Pin the tag to a concrete uv release during implementation (e.g. `:0.12.5-python3.11-bookworm-slim`) and document bumping it deliberately.
- **First cold build needs PyPI egress for `uv sync --frozen`** (hashes from lockfile, but wheels must download) → Same requirement pip already had; no regression. `--frozen` means **no resolution chatter**, only downloads.
- **Larger base/image than `python:3.11-slim`+pip-minimal** → Accepted: correctness + reproducibility over slimness; layers cache well.
- **`uv` image's `--no-install-project` still requires package metadata present** → `[build-system]` (D3) keeps this well-formed; project install is skipped anyway.
- **Worker on a separate podman machine** built from the same Dockerfile → No divergence; lockfile is committed, so both machines install identical deps.

## Migration Plan

1. Edit `pyproject.toml` (add `[build-system]`); `uv lock` unchanged (deps identical) — verify `uv lock --check` passes.
2. Rewrite `Dockerfile.web` and `Dockerfile.worker` per D1/D2/D4.
3. Build locally: `docker compose build` (or `docker build -f Dockerfile.web .`).
4. Verify: `docker compose up` → `curl localhost:5000/healthz` → `{"ok": true}`; submit a job; confirm worker processes it.
5. Update `README.md` Quickstart + `deploy/podman/README.md` wording (uv-based build).
6. **Rollback:** revert the Dockerfiles (git) → old pip images build again; both image tags remain `vataic-web`/`vataic-worker`, so quadlet units need no change either way.

## Open Questions

- Pin uv image to a specific version tag during implementation (recommended) or keep tracking `latest`? Default: pin.
