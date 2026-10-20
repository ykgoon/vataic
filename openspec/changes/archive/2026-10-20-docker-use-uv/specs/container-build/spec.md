# Container Build Specification

## ADDED Requirements

### Requirement: Dependencies installed from lockfile
Container images for `vataic-web` and `vataic-worker` SHALL install project dependencies with `uv` from the committed `uv.lock` using `uv sync --frozen`. Image builds MUST NOT resolve dependencies independently via pip.

#### Scenario: Build uses lockfile
- **WHEN** either container image is built
- **THEN** dependencies are installed from `uv.lock` with `uv sync --frozen` and no dev dependency group is installed

#### Scenario: Lockfile out of sync fails closed
- **WHEN** `pyproject.toml` and `uv.lock` are out of sync at build time
- **THEN** the build fails rather than silently re-resolving dependencies

### Requirement: Dependency layer cached separately from source
The image build SHALL install dependencies before copying application source so that application source changes do not invalidate the dependency layer.

#### Scenario: Source-only rebuild is fast
- **WHEN** only application source changes between builds
- **THEN** the dependency layer is reused from build cache and only the application source layer is rebuilt

### Requirement: Project is a valid PEP 517 package
`pyproject.toml` SHALL declare a `[build-system]` with `setuptools.build_meta` so the project can be built by any conforming backend.

#### Scenario: Build backend declared
- **WHEN** `pyproject.toml` is inspected
- **THEN** it contains a `[build-system]` section with a `setuptools.build_meta` backend

### Requirement: Runtime contract preserved
Both images SHALL preserve the existing runtime behavior: environment variables (`DATABASE_PATH`, `ARTIFACT_DIR`), the shared `/data` mount, and entrypoints — web runs `python -m app.migrate` then `gunicorn`, worker runs `python -m worker.main`. Gunicorn SHALL be available from the project venv without a separate install step.

#### Scenario: Web entrypoint unchanged
- **WHEN** the web container starts
- **THEN** migrations run and gunicorn serves the Flask app on 0.0.0.0:5000

#### Scenario: Worker entrypoint unchanged
- **WHEN** the worker container starts
- **THEN** `worker.main` polling loop starts

### Requirement: Compose and podman wiring unchanged
`docker-compose.yml` service definitions and `deploy/podman/*.container` units SHALL keep working with the rebuilt images without changes to their environment or mount wiring.

#### Scenario: Compose stack runs
- **WHEN** `docker compose up` is run with rebuilt images
- **THEN** web and worker start with the existing env, ports, and `/data` volume wiring

#### Scenario: Podman quadlet builds images
- **WHEN** `podman build -f Dockerfile.web .` and `-f Dockerfile.worker .` are run on the target machine
- **THEN** both images build successfully with the same tag names `vataic-web` and `vataic-worker`
