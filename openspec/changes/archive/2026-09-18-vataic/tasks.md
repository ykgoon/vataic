## 1. Project Scaffolding

- [x] 1.1 Initialize Flask application package structure (`app/`, `app/templates/`, `app/static/`, `worker/`) with a minimal runnable hello-world route
- [x] 1.2 Add dependencies: Flask, gunicorn, htmx (served locally), Temporal Python SDK, requests, numpy, pydantic, scikit-learn (optional), python-dotenv
- [x] 1.3 Create `.env.example` with LLM provider, models, budgets, timeouts, and SQLite path; document every variable
- [x] 1.4 Set up SQLite connection module with WAL mode, busy_timeout, and per-worker connection helpers
- [x] 1.5 Implement schema migrations directory (`migrations/`) with an initial schema migration and a bootstrap command that applies pending migrations on startup

## 2. Schema and Persistence Layer

- [x] 2.1 Create tables: `projects`, `projection_jobs`, `scenario_inputs`, `factor_sets`, `causal_graphs`, `world_states`
- [x] 2.2 Create tables: `particles`, `particle_states`, `projection_steps`, `llm_calls`, `checkpoints`
- [x] 2.3 Create tables: `outcomes`, `outcome_particle_memberships`, `artifacts`, `job_events`
- [x] 2.4 Create personal tables: `subjects`, `subject_profiles`, `personal_projection_settings`, `personal_factor_states`, `personal_events`, `safety_flags`
- [x] 2.5 Create `settings` table for provider, budgets, thresholds, role-model mapping; seed defaults
- [x] 2.6 Implement `job_repository` with CRUD for jobs, states, transitions, and event log appending
- [x] 2.7 Implement localStorage/artifact store writing large JSON payloads under `data/artifacts/` with references stored in DB rows
- [x] 2.8 Write integration tests for schema creation, migrations applying cleanly, and idempotent seed

## 3. Job State Machine and Checkpointing

- [x] 3.1 Implement job lifecycle constants (DRAFT, QUEUED, INITIALIZING, RUNNING, CHECKPOINTING, RECOVERING, CLUSTERING, EXPLAINING, COMPLETED, FAILED, CANCELLED, SUSPENDED) and validation of allowed transitions
- [x] 3.2 Implement `checkpoint_repository`: save/load latest checkpoint, list checkpoints, mark immutable
- [x] 3.3 Implement lease/heartbeat module: acquire, renew, expire, take-over for a job id
- [x] 3.4 Implement idempotency-key helpers composing deterministic keys (job, phase, step, particle, context hash)
- [x] 3.5 Implement recovery scanner: find expired-lease RUNNING/RECOVERING jobs, mark RECOVERING, resume from latest checkpoint
- [x] 3.6 Implement budget tracking: read/write counters (llm_calls, tokens, elapsed) with each checkpoint and check fenced by budget caps
- [x] 3.7 Test: state transitions invalid → rejected; crashed job resumes from checkpoint not step 0; budget-exhausted job fails cleanly

## 4. Temporal Worker Integration

- [x] 4.1 Create `ProjectionJobWorkflow` with activities mapped to phases: parse_scenario, extract_axioms, select_factors, build_causal_graph, initialize_world_state, projection_loop, cluster_outcomes, generate_explanations, finalize_report
- [x] 4.2 Implement workflow self-reinvocation pattern (continue-as-new / next-iteration) so the loop re-enters until checkpoint phase flips to clustering
- [x] 4.3 Ensure every activity writes its result to DB before completing and is idempotent w.r.t. its key
- [x] 4.4 Wire worker entrypoint (`worker/` package) that starts the Temporal worker and connects to the server from env
- [x] 4.5 Add Temporal activity timeouts and retry policy matching budget config
- [x] 4.6 Test: activity failure retried without duplicate effects; worker restart resumes in-flight job

## 5. LLM Gateway

- [x] 5.1 Implement `LLMClient` abstraction with OpenAI-compatible chat-completions provider (base_url, api_key from env, model)
- [x] 5.2 Implement role-based model routing (strong/cheap/embedding) with fallback to primary model
- [x] 5.3 Implement structured request hashing and cache lookup/write into `llm_calls` (cache_hit/cache_miss semantics)
- [x] 5.4 Implement retry with exponential backoff and per-call attempt budget
- [x] 5.5 Build Pydantic response validators per phase (parse, factor selection, causal graph, projection step, epoch summary, cluster, explanation)
- [x] 5.6 Test: cached response reused across resume; invalid JSON retried then failed; provider switch via settings

## 6. Scenario Intake

- [x] 6.1 Implement scenario parsing step: premise → premise_type, axioms, affected entities/systems, reality mode, horizon with documented defaults
- [x] 6.2 Implement ambiguity handling with recorded assumptions and defaults
- [x] 6.3 Implement reality-mode plumbing (grounded/speculative/fantastical) into downstream validators
- [x] 6.4 Implement personal crisis-topic detection gate (keyword + LLM classifier) halting personal-scope projections with safety resources
- [x] 6.5 Test: fantastical premise accepted; missing horizon defaults to auto; crisis premise halted

## 7. Factor Selection and Causal Model

- [x] 7.1 Implement impact-surface catalog (14 surfaces) and factor importance scoring formula
- [x] 7.2 Implement factor selection step producing factor set, discarded list, and weak-signal watchlist within configured limits (macro 8–15/20, personal 10–18)
- [x] 7.3 Implement causal graph builder + validator (bounds, cycles, delays, implausible strengths, unknown factors)
- [x] 7.4 Implement critic pass for causal graph contradictions with revision request loop
- [x] 7.5 Implement initial world state construction with factor normalization (0–100) and persistence as step 0
- [x] 7.6 Test: factor cap enforced; invalid edge rejected; contradictory cycle flagged and revised

## 8. Projection Engine

- [x] 8.1 Implement compressed context builder (scenario card, factor state, pinned turning points, epoch summary, causal digest) within token budget
- [x] 8.2 Implement development proposer (forecaster) calling LLM with structured output for next step
- [x] 8.3 Implement development validation (bounds, probability normalization, enum checks, contradiction scan) and critic rejection
- [x] 8.4 Implement adaptive time-step selector (base step by domain/volatility, bounded min/max, LLM recommendation clamp)
- [x] 8.5 Implement particle expansion: apply drift vs branch vs tail-risk branching per thresholds
- [x] 8.6 Implement merging (normalized factor-state distance below threshold → sum probability mass) and pruning (beam width, redundancy, inconsistency, stagnation)
- [x] 8.7 Implement stopping-rule evaluation at every checkpoint (horizon, convergence, divergence threshold, diminished novelty, budget, user depth)
- [x] 8.8 Implement per-step checkpointing and particle state writes for step N+1
- [x] 8.9 Test: beam width respected after merge/prune; interruption mid-step resumes exactly; adaptive steps respect bounds

## 9. Memory Compression

- [x] 9.1 Implement three-tier memory model and epoch boundary detection (configurable intervals, macro vs personal)
- [x] 9.2 Implement historian step producing cached, idempotent epoch summaries
- [x] 9.3 Implement pinned turning-point management (add, compress, retain in every context)
- [x] 9.4 Implement token-budget-aware context truncation (scenario card > state > turning points > epoch summary order)
- [x] 9.5 Test: raw events retained in store while contexts reference summaries; turning points persist across contexts

## 10. Outcome Synthesis

- [x] 10.1 Implement particle vectorization and numeric clustering (agglomerative or numpy k-means fallback)
- [x] 10.2 Implement LLM-assisted cluster review, labeling, and low-quality cluster merging
- [x] 10.3 Implement probability aggregation and normalization across outcome clusters
- [x] 10.4 Implement representative trace selection and retention of alternative particle paths
- [x] 10.5 Implement explainer step generating per-outcome narratives (what/how likely/caused/factors/signals/assumptions/invalidation)
- [x] 10.6 Implement probability bands (dominant/plausible/alternative/tail) and disclaimer
- [x] 10.7 Implement personal outcome enrichment: decision levers, protective actions, warning signs, support needed, wellbeing risks, decision map
- [x] 10.8 Test: weights normalize to 1; adjacent clusters merged; representative path + alternatives available

## 11. Web UI (htmx)

- [x] 11.1 Implement scenario submission form (premise + optional settings) and async job-accepted notice
- [x] 11.2 Implement jobs dashboard with htmx polling partials (status, progress, last checkpoint, error state, retry action)
- [x] 11.3 Implement job detail page with phase, checkpoint, budget usage, and cancel/suspend/resume/retry controls via htmx POST
- [x] 11.4 Implement assumption/factor review page pre-projection with save-before-loop edits
- [x] 11.5 Implement result explorer: outcome cards, expandable explanations, timeline tree, factor trajectory SVG charts (all htmx swaps, no full reload)
- [x] 11.6 Implement personal scenario builder, privacy controls, export/delete actions, and safety interstitial
- [x] 11.7 Implement settings panel for LLM provider, budgets, thresholds, role models
- [x] 11.8 Test: dashboard polls live progress; branch node click swaps detail pane; personal export produces artifact bundle

## 12. Deployment and Packaging

- [x] 12.1 Create `docker-compose.yml` with web (gunicorn) and worker services, volume for `./data`, Temporal dev-server service (sqlite-backed)
- [x] 12.2 Create Dockerfiles for web and worker images from the same base
- [x] 12.3 Create `.env.example`-driven compose wiring; ensure schema auto-migrates on web startup
- [x] 12.4 Create `deploy/podman/` Quadlet units (`.container`, `.service`, `.network`, `.volume`) and a README with deploy/verify instructions for a different machine (not testable here)
- [x] 12.5 Add a smoke-test script: submit small scenario, watch phases, kill worker mid-run, confirm resume
- [x] 12.6 Add repo README with architecture summary, quickstart, config reference, and limitations

## 13. Verification and Polish

- [x] 13.1 Run full test suite and fix failures across all modules
- [x] 13.2 Validate against durability specs: resume after kill, LLM cache reuse, idempotent retries
- [x] 13.3 Validate outcome synthesis produces explainable, non-broken outcome cards with probabilities summing to 1
- [x] 13.4 Validate personal projection end-to-end (builder → factors → projection → decision map → outcome enrichment)
- [x] 13.5 Confirm liquidity: `docker compose up` works, dashboard renders, and a full overnight-style job completes or stops by budget