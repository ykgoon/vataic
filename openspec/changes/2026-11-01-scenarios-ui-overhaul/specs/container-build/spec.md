# Container Build — Delta Spec

## ADDED Requirements

### Requirement: Source reload without rebuild
The compose development stack SHALL bind-mount the application source (`app/`, `worker/`, `migrations/`) into both containers, so that logic and template changes are loaded by `docker compose restart` without an image rebuild. Image rebuilds SHALL be required only when project dependencies change (uv.lock / pyproject.toml).

#### Scenario: Template edit loads on restart
- **WHEN** a template or Python file under the mounted source changes and `docker compose restart` is run
- **THEN** the running web and worker containers serve the changed code without an image build

#### Scenario: Dependency change still requires rebuild
- **WHEN** a dependency is added to pyproject.toml
- **THEN** the images are rebuilt (uv sync layer) and the compose stack recreated

## MODIFIED Requirements

### Requirement: Compose and podman wiring unchanged
`docker-compose.yml` service definitions SHALL keep working with the rebuilt images; the compose stack MAY additionally bind-mount source per the source-reload requirement. `deploy/podman/*.container` units SHALL keep working with the baked images without changes to their environment or mount wiring.

#### Scenario: Compose stack runs
- **WHEN** `docker compose up -d` is run
- **THEN** web and worker start with the existing env, ports, and `/data` volume wiring

#### Scenario: Podman quadlet builds images
- **WHEN** `podman build -f Dockerfile.web .` and `-f Dockerfile.worker .` are run on the target machine
- **THEN** both images build successfully with the same tag names `vataic-web` and `vataic-worker`
