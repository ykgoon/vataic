## Context

Greenfield build of "Vataic" (a "world projector"), a web-based asynchronous scenario laboratory. It accepts any premise (macro/personal/speculative/fantastical), extracts consequential factors, builds a causal model, and runs an overnight projection loop producing 5–9 explainable outcome worlds. Projection runs may span hours, hardware may shut down mid-run, and inputs/intermediate states/final results must all be durably stored and resumable.

Stack is fixed by the project constraints:
- Flask backend (not FastAPI/Django)
- htmx frontend (not Next.js)
- SQLite (not Postgres)
- Temporal for durable workflows
- Configurable LLM provider
- No user authentication, no remote storage
- Docker Compose for local dev; Podman Quadlet manifests for deployment on other machines (untestable here)

This is an MVP-ish but production-shaped system: long-running durable jobs, checkpoint/resume, LLM call caching, and structured LLM-driven simulation.

## Goals / Non-Goals

**Goals:**
- Accept any premise and produce explainable, clustered future outcomes.
- Survive crashes/restarts: jobs resume from last committed checkpoint.
- Keep LLM context bounded via three-tier memory and pinned turning points.
- Keep timeline count bounded via beam width + merge + prune.
- Domain-agnostic factor system (impact surfaces, not hardcoded economics).
- Both macro and personal scopes with distinct state, factors, and outcomes.
- Fully self-hosted, local storage only, no auth.

**Non-Goals:**
- Live data feeds / web scraping for grounded state.
- Real user authentication or multi-tenant authN/authZ.
- Distributed/remote object storage (local disk + SQLite only).
- Real-time collaboration or high-fidelity visualizations (SVG/htmx-level UI is enough).
- Perfect Bayesian calibration; weights are subjective scenario weights.
- Guaranteed correctness of predictions; output is scenario exploration.

## Decisions

### D1: SQLite as system of record with WAL and a local artifacts dir
All structured state (jobs, factors, particles, steps, checkpoints, llm_calls, outcomes) lives in a single SQLite database. Large payloads (LLM request/response bodies, full reports, exports) are stored as JSON on local disk under `data/artifacts/` with paths referenced in DB rows.

- **Why**: No remote storage allowed; single-node deployment; SQLite gives ACID transactions, zero-admin ops, and survives power loss with WAL. JSON blobs keep flexible factor/state schemas without heavy migrations.
- **Alternatives considered**: Postgres (rejected by constraints), JSON files only (loses atomicity/queryability), object storage (rejected — remote).
- **Trade-off**: SQLite writes serialize; mitigated with WAL, busy_timeout, short transactions, and separate connection per worker.

### D2: Temporal as the durable workflow engine
Jobs execute as Temporal workflows (`ProjectionJob`) with activities: parse_scenario → extract_axioms → select_factors → build_causal_graph → initialize_world_state → projection_loop (a child/loop activity, one execution per step, each synchronous in the DB) → cluster_outcomes → generate_explanations → finalize_report. Each activity is idempotent and retried by Temporal; every activity persists its output before completing.

- **Why**: Constraint requires Temporal. Provides retries, timeouts, worker-crash recovery, and multi-hour execution for free. Temporal's event-history (task failures) is our audit trail; our DB checkpoints are the resume source of truth.
- **Alternatives considered**: Custom DB job runner (more engineering for recovery), Celery (weak checkpointing), Timeline (beta, dockerhub-only).
- **Trade-off**: Adds an infra dependency (Temporal server + Postgres backing store — but see D4). Requires a `temporal worker` sidecar always running on the app host.

### D3: Loop-as-checkpoint-resolution (state machine in DB, not nested workflow)
The projection loop is not a giant Temporal loop with thousands of steps. Instead each "step" is a Temporal activity invocation that:
1. Acquires a lease (lease table).
2. Loads latest checkpoint from DB.
3. Determines next pending step index (or phase).
4. Executes exactly one durable unit of work (e.g., one projection step across active particles, or a phase like causal-graph build).
5. Writes new checkpoint + appends event-log row.
6. Completes. The workflow self-reinvokes (continue-as-new / next iteration) until a stop condition flips the checkpoint phase to `clustering`.

- **Why**: Keeps Temporal activities short and crash-safe, makes resume trivial (just re-enter with the DB checkpoint as truth), and keeps the "database is truth" rule central. Avoids Temporal history bloat for 40+ steps.
- **Alternatives considered**: One long-running activity looping internally (loses durable per-step resume, risks heartbeats); fully DAG-expanded workflow (combinatorial node count).
- **Trade-off**: More bookkeeping; mitigated by a tiny checkpoint FSM.

### D4: Temporal server with its own SQLite-backed datastore for local dev
For the default `docker compose up`, run Temporal Server in dev mode (which itself supports a sqlite-backed storage, `temporal` CLI dev server). Database migrations via `temporal-sqlite-tool` dev target. Production-Podman path can run Temporal with its Postgres/Elasticsearch, but the application still uses SQLite; the Podman Quadlet manifests document this.

- **Why**: Local dev must be zero-friction; a sqlite-backed Temporal dev server keeps compose small while still shipping on the same Temporal APIs.
- **Alternative**: Require Postgres just for Temporal (violates the constraint spirit of local simplicity).
- **Trade-off**: Dev-mode Temporal server is not suitable for multi-TB production scale; acceptable for self-hosted single-machine use, documented as such.

### D5: LLM gateway is provider-agnostic, OpenAI-compatible by default
A single `LLMClient` abstraction with:
- provider enum: openai-compatible (covers OpenAI, Anthropic via base_url proxies, Ollama, LM Studio, vLLM, etc.), plus optional `anthropic` native.
- settings table / env: `provider`, `base_url`, `api_key` (env-only, never stored), primary + role model names.
- structured outputs via `response_format=json_object` or JSON-mode prompts + Pydantic validation.
- request hashing → cache lookup in `llm_calls` (cache_hit/cache_miss).
- exponential backoff retry w/ attempt budget.

- **Why**: Constraint says "allow place to set LLM provider". OpenAI-compatible chat-completions is the widest common denominator, so most providers work without code changes.
- **Alternatives**: Vendor-specific SDKs for every provider (maintenance burden, unnecessary when API-compatible), langchain (heavy dependency).
- **Trade-off**: Some providers are not perfectly OpenAI-compatible; a thin adapter seam (goose) is kept so a new provider is a small class, not a rewrite.

### D6: Pure SQLite + numpy for the deterministic parts; LLM for the semantic parts
Geometry/clustering/distance use numpy on factor vectors; similarity distances, k-means/agglomerative clustering run in Python. Semantic labeling, explanations, epoch summaries use LLM. Validation of LLM proposals is rule-based (bounds, enum checks, probability normalization) plus an optional critic LLM call for contradictions.

- **Why**: Cheap, deterministic, testable; the design doc (and proposal) explicitly want rules-engine responsibilities separate from LLM responsibilities.
- **Alternatives**: LLM-only everything (non-deterministic, drift between runs), full numeric sim (overkill for MVP).
- **Trade-off**: Clustering quality depends on vector normalization; acceptable since factors are normalized 0–100.

### D7: State machine with explicit phases and recoverable transitions
Shared enum: `DRAFT, QUEUED, INITIALIZING, RUNNING, CHECKPOINTING, RECOVERING, CLUSTERING, EXPLAINING, COMPLETED, FAILED, CANCELLED, SUSPENDED`. Every transition persisted as `job_events` row.

- **Why**: Required by durable-jobs spec; makes crash-recovery observable via dashboards; one source of truth for where a job is.
- **Alternatives**: Implicit "active" flag + phase string.
- **Trade-off**: Slightly more writes; negligible.

### D8: htmx server-rendered UI (templates + partials), no JS framework
Jinja2 templates + htmx for dashboards, result explorer, timeline tree (nested `<ol>`/CSS/SVG), factor charts (inline SVG or a tiny JS on `canvas`). Auto-refresh via htmx `hx-get` polling partials.

- **Why**: Constraint (htmx, not Next.js); schema impervious to frontend build tooling; Flask + htmx is low-latency and simple.
- **Alternatives considered**: Next.js/React (rejected by constraint), plain JS (htmx chosen).
- **Trade-off**: Complex interactive visualizations limit; mitigated via SVG charts + htmx targeted swaps.

### D9: Checkpoint granularity = after every projection step; event log + snapshot both
Two persistence mechanisms: append-only `job_events` (audit/recovery) + full checkpoints table (fast resume). Idempotency keys composited from job/phase/context-hash.

- **Why**: Per-step checkpointing prevents losing expensive LLM-call work; matches spec and crash-resume requirements.
- **Alternatives**: Interval-based checkpoints only.
- **Trade-off**: More writes but small rows; fine.

### D10: Agent composition as one orchestrator with role-scoped prompts (not separate services)
The 9-agent model from the design doc is implemented as a single Python `steps/` package with distinct callables per phase (scenario_architect, factor_analyst, causal_modeler, forecaster, quant, critic, historian, outcome_cartographer, explainer) each with its own prompt template + validator. No separate microservices.

- **Why**: Single-worker deployment keeps temporal orchestration simple; roles are function boundaries, not processes.
- **Alternatives**: Multi-service agent fleet (ops burden, fails single-machine goal).
- **Trade-off**: Less horizontal scaling; acceptable at self-hosted scale.

### D11: Personal safety gate at intake + data controls
A crisis-topic detection step (keyword + LLM classifier) runs at scenario intake; on flag it halts the job with safety resources and refuses projection. Personal data subject to export/delete endpoints. Ethics disclaimer shown on all outcome content. Personal subjects private-by-default scope in DB.

- **Why**: Personal-scope requirements demand data minimization, consent/control, and crisis handling; shipping a hard gate prevents the tool from producing harmful "projections" for crisis premises.
- **Alternatives**: None reasonable for consumer personal data.

### D12: Deployment: Docker Compose (dev) + Podman Quadlet manifests (transplant)
- `docker-compose.yml`: web (Flask/gunicorn), worker (Temporal worker), temporal server (dev sqlite mode), plus volume for `./data`.
- `deploy/podman/`: `.container`, `.service`, `.network`, `.volume` Quadlet units + `docker-compose.yml`-equivalent for Podman and README notes. Not testable here — shipped as scaffolding with verification instructions for the target machine.

- **Why**: Constraint. Compose for local dev; Quadlet is the modern systemd-native Podman unit format for a different machine.
- **Trade-off**: Two deployment manifests to keep in sync; mitigated by deriving Quadlet from the same image tags/env spec.

### D13: Budget caps as first-class config
Per-job budget object (max_steps, max_branches, max_llm_calls, max_tokens, max_runtime_seconds) read from job settings with defaults from settings table; checked in the loop before/after each step; enforced by fencing off further Temporal self-invocation.

- **Why**: Stopping rules spec requires it; prevents overnight runs from running away.
- **Alternatives**: None sane.

## Risks / Trade-offs

- [SQLite write contention under simultaneous particles/steps] → WAL mode, busy_timeout, single writer per job serialized by checkpoint, short transactions; load tests with 20 particles.
- [Temporal dev server sqlite storage is not production-grade] → documented; Podman deployment path allows Postgres for Temporal only; app DB stays SQLite.
- [LLM non-determinism → divergent reruns] → seed + deterministic context hash + caching + versioned prompt templates; caching provides deterministic replays.
- [Context budget overflow on long horizons] → configurable token budget, epoch-summary truncation tier last, pinned turning points compressed.
- [Personal data privacy breach / leaks] → local-only storage, no auth (single user), private-by-default scoping, export/delete endpoints, no external persistence.
- [Beam width too small → missing interesting futures] → hybrid slots (top-probability + reserved divergent slots), configurable.
- [Drift between prompt templates in different phases] → single `prompts/` dir with registered templates + tests asserting each phase's required sections.
- [htmx UI limited for rich visualizations] → acceptable; use SVG charts + htmx targeted swap; document that only one detail pane is live at a time.
- [Provider API breakage (non-OpenAI-compatible endpoints)] → adapter seam; provider-specific client classes behind `LLMClient`; a new provider = new class, not rewrite.
- [Failure to stop (runaway loop)] → budget caps + loop invariant checks at every step; Temporal timeout on each activity.

## Migration Plan

Greenfield: no migration from existing system. Deploy steps are provisioning:
1. `docker compose up` starts Temporal (dev) + build web/worker images.
2. Flask app runs schema migrations on startup (`flask db` / raw `ALTER` migration files in a `migrations/` dir).
3. Settings bootstrap from `.env`; defaults seeded by a `seed_settings` command.
4. First projection job is the smoke test: submit a small scenario, watch job phases on the dashboard, kill the worker container mid-run, confirm resume.
5. For deployment on another machine: copy repo, build the same images (or export them), install Podman Quadlet units from `deploy/podman/`, run `systemctl --user start vataic-web`, point `.env` at a Temporal Postgres (optional) — follow README in `deploy/podman/`.

Rollback: all job state in `data/`; removing images/volumes leaves DB intact; previous app version can be re-run against same volume (checkpoint-tolerant design).

## Open Questions

- Should the default projection horizon be strictly capped (e.g., 5yr) or fully "auto until convergence/diminished novelty"? → default to 5yr cap, auto earlier; user override accepted.
- Should the LLM gateway support non-OpenAI-compatible providers in MVP (e.g., native Anthropic message API), or ship OpenAI-compatible-only with adapter seam for later? → MVP: OpenAI-compatible only + seam.
- Which embedding/clustering library: `scikit-learn` (agglomerative) vs pure numpy + manual distance threshold? → use scikit-learn if available in worker image; fall back to numpy-only k-means otherwise.
- Content safety: should crisis detection run for macro premises too, or personal-scope only? → personal-scope only for MVP, log-only for macro.
- Multi-user story: none (no auth). Should a single job be treated as "the" workspace, or keep job-switcher UI? → job-switcher UI, no accounts, all jobs visible locally.