# Futures Output Redesign — Tasks

## 1. Futures-view artifact builder

- [x] 1.1 Create `app/futures.py` with aggregate computation: per outcome, per factor, per step mean/min/max across member particles from `particle_states`; end-state deltas vs baseline; member id + weight lists
  - Acceptance: pure function of (job data) → dict; unit tests cover a synthetic 3-outcome job fixture
  - Verify: `uv run pytest tests/test_futures.py`
  - Files: `app/futures.py`, `tests/test_futures.py`

- [x] 1.2 Implement branch-point detection from `particles.parent_id` lineage (permanent divergence = siblings' descendants end in different outcomes); record step, outcomes involved, per-factor deltas at fork, stored turning points/events at that step; empty list when no divergence
  - Acceptance: synthetic fixtures for fork, no-fork, and re-merge lineages produce expected branch lists
  - Verify: `uv run pytest tests/test_futures.py -k branch`
  - Files: `app/futures.py`, `tests/test_futures.py`

- [x] 1.3 Derive story steps with degradation tiers (events → step summaries → factor milestones) and record `story_source` in the artifact; no invented events at tier 3
  - Acceptance: fixture with empty events/summaries yields milestone-only steps tagged `story_source: milestones`
  - Verify: `uv run pytest tests/test_futures.py -k story`
  - Files: `app/futures.py`

- [x] 1.4 Artifact persistence: store as `artifacts` row (kind `futures_view`), versioned JSON; `load_futures_view(jid)` with lazy heal on miss; stale-artifact rebuild keyed on job `updated_at`
  - Acceptance: second load hits the stored artifact; touching the job row triggers rebuild
  - Verify: `uv run pytest tests/test_futures.py -k artifact`
  - Files: `app/futures.py`

## 2. Completion hook

- [x] 2.1 Invoke the builder at job completion in the synthesis phase; wrap writes in `_write_retry`; artifact failure logs and does not fail the job
  - Acceptance: a completed job has a `futures_view` artifact; injected builder exception leaves the job COMPLETED
  - Verify: `uv run pytest tests/test_futures.py -k completion` and existing suite
  - Files: `app/steps/synthesis.py`, `worker/workflows.py`

## 3. Futures view routes and SVG

- [x] 3.1 Rework `GET /results/<jid>` to render from the artifact: overview strip (premise, horizon label, timelines explored, distinct futures, heuristic-mode badge from zero `llm_calls`), probability split with share-and-count, sorted by probability
  - Acceptance: rendered page matches mock1.png structure for the devbox job; heuristic badge appears on the 0-LLM job
  - Verify: manual via devbox; `uv run pytest tests/test_routes_futures.py`
  - Files: `app/routes/results.py`, `app/templates/results/index.html`

- [x] 3.2 Factor fan chart: server-rendered SVG (mean + min/max band per outcome, dashed baseline, time labels with step-index fallback); htmx factor-tab partial swap
  - Acceptance: switching factor tab updates chart without full page load
  - Verify: `uv run pytest tests/test_routes_futures.py -k fan`; manual
  - Files: `app/routes/results.py`, `app/templates/results/partials/fan.html`

- [x] 3.3 Outcome cards as futures: name, band, share-and-count, arc summary, delta chips with direction indicators, collapsed watch-for/assumes/risks/causal-path via native `<details>`
  - Acceptance: cards render collapsed detail sections; delta chips match artifact end-state deltas
  - Verify: `uv run pytest tests/test_routes_futures.py -k cards`
  - Files: `app/templates/results/index.html`, `app/present.py`

- [x] 3.4 Futures map SVG: origin trunk, one weighted path per outcome colored by band, cornerstones at detected forks with fork labels; single path when no divergence; path selection swaps story via htmx
  - Acceptance: map renders for fork and no-fork fixtures; click swaps story without reload
  - Verify: `uv run pytest tests/test_routes_futures.py -k map`; manual
  - Files: `app/routes/results.py`, `app/templates/results/partials/map.html`

- [x] 3.5 Path story partial: numbered time-anchored steps with cornerstone click-to-expand rationale (`<details>`): causal chain, factor deltas, dependent assumptions; early-signals callout from indicators; heuristic-mode source shown when `story_source` is milestones
  - Acceptance: rationale disclosures render non-empty for both LLM and heuristic jobs
  - Verify: `uv run pytest tests/test_routes_futures.py -k story`; manual
  - Files: `app/templates/results/partials/story.html`, `app/routes/results.py`

## 4. Scope-aware phrasing

- [x] 4.1 Scope phrasebook (personal = second person, macro = third person) applied to outcome names/summaries/story rendering from neutral artifact data
  - Acceptance: same fixture rendered under both scopes differs only in phrasing
  - Verify: `uv run pytest tests/test_routes_futures.py -k scope`
  - Files: `app/templates/results/` phrasebook macros or `app/present.py`

## 5. Navigation and cleanup

- [x] 5.1 Remove timeline tree from primary navigation; keep `/results/<jid>/tree` as debug route; base nav and results header updated
  - Acceptance: no primary link to tree; debug route still renders
  - Verify: `uv run pytest` (existing tree tests unchanged); manual nav check
  - Files: `app/templates/base.html`, `app/templates/results/index.html`, `app/routes/results.py`

## 6. End-to-end verification on devbox

- [x] 6.1 Deploy to devbox compose stack (source hot-patch or rebuild during a quiet period — never concurrent builds); rerun or reuse a completed job; screenshot the futures view, fan chart, map, story, and rationale disclosures with the headless-Chrome setup in `/tmp/vshot`
  - Acceptance: screenshots match the approved mockups' structure; legacy job heals on first view; zero-LLM job shows heuristic badge
  - Verify: visual review; screenshots stored in `vataic/screens/`
  - Files: none (verification)
