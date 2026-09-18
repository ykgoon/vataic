# Vataic

## Why

A tool is needed that takes any hypothetical premise (macro, personal, speculative, or fantastical), logically deduces possible futures over time, and presents them as a small set of explainable outcome worlds. Raw timeline simulation explodes combinatorially and raw LLM guessing is not disciplined, so the system must be a structured engine where an LLM proposes plausible developments inside a state-managed simulation. Jobs can run for hours (overnight), may be interrupted by hardware shutdowns, and must resume from checkpoints rather than restart.

## What Changes

- New application "Vataic": a web-based asynchronous scenario laboratory ("world projector").
- Accepts arbitrary premises, converts them to axioms, and selects a finite set of consequential domain-agnostic factors (impact surfaces).
- Builds a causal graph and initial world state; runs a projection loop over adaptive time steps.
- Represents timelines as particles that branch, merge, and prune under beam-search control.
- Supports both macro and personal scope, including subject cards, agency assumptions, and behavioral feedback loops.
- Compresses LLM context via a three-tier memory model (scenario card, current state, epoch summaries) plus pinned turning points.
- Runs as durable Temporal workflows with per-step checkpoints, idempotent operations, LLM call caching, and lease/heartbeat recovery so interrupted jobs resume.
- Persists everything (inputs, axioms, factors, causal graph, steps, particles, LLM calls, checkpoints, outcomes, reports) in SQLite.
- Clusters final particles into 5–9 named outcomes with probability weights, causal paths, leading indicators, assumptions, and explainable narratives.
- Presents a job dashboard (submit, progress, resume/retry, cancel) and a result explorer (outcome cards, timeline tree, factor trajectories) via server-rendered htmx pages.
- Configurable LLM provider (provider, base URL, model, keys) via environment/settings panel; no hardcoded vendor.
- Self-hosted via Docker Compose; deployable on other machines via Podman Quadlet (not testable in this environment).
- **No new capabilities are being removed; this is a greenfield build.**

## Capabilities

### New Capabilities
- `scenario-intake`: Parse arbitrary user premises into structured scenarios, axioms, impact surfaces, and reality modes; handle ambiguity via defaults or clarifying questions.
- `projection-engine`: Select consequential factors, build and validate causal graphs, run the projection loop over adaptive time steps with branching, merging, pruning, stopping rules, and budget caps.
- `personal-projection`: Subject cards, environment context, personal factor categories, agency assumptions, decision points, and life-trajectory outcomes with wellbeing dimensions and safety handling.
- `durable-jobs`: Durable Temporal workflows with job lifecycle state machine, per-step checkpoints, idempotent activities, LLM call caching, leases/heartbeats, resume-after-crash, and budget tracking.
- `memory-compression`: Three-tier compressed context (scenario card, current factor state, epoch summaries) with pinned turning points for LLM calls.
- `outcome-synthesis`: Cluster final particles into 5–9 named outcomes, compute normalized probability weights, generate explainable narratives, causal paths, leading indicators, and assumptions.
- `llm-gateway`: Provider-agnostic LLM interface with structured JSON output, request hashing/caching, retries with exponential backoff, validation, and model routing.
- `web-ui`: Server-rendered htmx interface for scenario submission, job dashboard with live progress, result explorer (outcome cards, timeline tree, factor trajectories), and settings for LLM provider.

### Modified Capabilities
<!-- None - greenfield project, no existing specs to modify. -->

## Impact

- **New codebase**: Flask application, htmx templates, SQLite schema (SQLAlchemy or raw sqlite3), Temporal worker, LLM gateway client.
- **Infrastructure**: Docker Compose for Flask app + Temporal server + worker(s); Podman Quadlet manifests for alternate machine deployment.
- **External dependencies**: Temporal server, LLM API/hosted endpoint (provider configurable).
- **Configuration**: Environment-driven settings (LLM provider, model names, budgets, timeouts). `.env.example` shipped.
- **Storage**: Local SQLite file(s) only. No remote storage; large payloads stored locally on disk with references in DB. No user authentication.