## Why

Docker image builds are broken and the container pipeline drifted from the rest of the repo: local dev is fully `uv`-based (`uv.lock` is the pinned, hashed source of truth, `uv`-managed Python 3.11), but both `Dockerfile.web` and `Dockerfile.worker` still hand-roll `pip install -e .` against a stale copy of `pyproject.toml` that has no `[build-system]` — an editable install of an empty tree before `app/` even exists. Two uncoordinated package managers create a fragile, often-failing build path and a second (unpinned) dependency resolution.

## What Changes

- Rework `Dockerfile.web` and `Dockerfile.worker` to install dependencies with `uv` from the committed lockfile (`uv sync --frozen`) instead of pip + editable install.
- Use `uv`'s official image (`ghcr.io/astral-sh/uv:python3.11-bookworm-slim`) and/or its `COPY --from` pattern so the exact Python + uv runtime from the lockfile is used.
- Add a `[build-system]` (setuptools) to `pyproject.toml` so the project is a properly declarable PEP 517 package.
- Retire the redundant `pip install gunicorn` line (gunicorn already a project dependency).
- Keep the same runtime behavior: `python -m app.migrate` then `gunicorn` (web); `python -m worker.main` (worker); identical env vars, entrypoints, and compose wiring.
- Update `deploy/podman/README.md` and `README.md` Quickstart to reflect the uv-based image build.

## Capabilities

### New Capabilities
- `container-build`: reproducible Docker/Podman image builds for `web` and `worker` that resolve dependencies from `uv.lock` via `uv`, matching the local dev environment.

### Modified Capabilities
<!-- No existing spec is affected at the requirement level. -->

## Impact

- **Images**: `Dockerfile.web`, `Dockerfile.worker` rewritten.
- **Docker/Podman builds**: `docker-compose.yml` env wiring unchanged; `deploy/podman/*.container` unchanged (image tags stay `vataic-web`/`vataic-worker`).
- **Project packaging**: `pyproject.toml` gains `[build-system]`.
- **Docs**: `README.md`, `deploy/podman/README.md`.
- **Dependency resolution**: now solely from `uv.lock` (hash-pinned) — removes pip's unpinned fallback resolution.
- **Build time**: cold builds larger (uv layer with full deps) but cached separately from source → faster rebuilds.
