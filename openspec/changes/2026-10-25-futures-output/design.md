# Futures Output Redesign — Design

## Context

The projection pipeline already stores everything a legible futures view needs:

- `particles` (id, parent_id, probability, factor_state, created_step, status) — the
  timeline population with real lineage.
- `particle_states` (particle_id, step_index, factor_values, events) — per-step factor
  values for every timeline; `events` may be empty (heuristic-mode jobs).
- `projection_steps` (step_index, time_label, summary, turning_points) — shared step
  narrative; may be template-generated.
- `outcomes` + `outcome_particle_memberships` — clusters with probability, band, summary,
  causal_path, indicators, assumptions, risks, explanation, representative_particle.
- `factor_sets.factors` — id → human-name mapping.
- `world_states` — baseline factor values (step 0).

Current output gaps: results page shows end-state scores only; the tree page renders raw
particle ids; nothing shows *how* a future unfolds or *where* timelines split.

Approved mockups: `screens/mock1.png` (overview), `screens/mock2.png` (map + story).

## Goals / Non-Goals

**Goals:**
- One futures page answering: what could happen? how would it unfold? when do paths split?
- Cornerstones (branch points, pivotal story steps) carry click-to-expand rationales.
- Scope-aware phrasing (personal = "you", macro = third person).
- Deterministic rendering from stored data; graceful degradation when LLM narrative is
  absent; heuristic-mode disclosure.
- Stay on the current architecture: Flask + Jinja + htmx + server-rendered SVG, native
  `<details>` for disclosure, no JS framework, no client chart libs.

**Non-Goals:**
- Multi-job comparison; drag/zoom interactivity; client-side rendering; changes to the
  projection engine, clustering, or intake; new LLM calls at render time.

## Decisions

### D1: Precompute a futures-view artifact instead of aggregating per request
A completed job has up to ~50 particles × 40 steps × ~8 factors of state rows. Aggregating
that on every page view is wasteful and slow. At completion (and lazily for legacy jobs),
a builder module computes a versioned JSON artifact: per outcome {mean/min/max per factor
per step, member particle ids + weights, end-state deltas}, plus the branch-point list
with per-fork factor deltas and step events. Stored via the existing `artifacts` table
(kind `futures_view`, version field inside the JSON payload).

**Alternative considered:** aggregate in the route on every request with an in-process
LRU cache. Rejected: cache invalidation across gunicorn workers, and legacy-job healing
would recompute invisibly; the artifact also doubles as an exportable summary.

### D2: Branch points from lineage, not guessed
A branch point is detected from `particles.parent_id`: group particles by (parent, step
created); when siblings' descendant sets land in different outcomes at completion, that
fork is permanent and meaningful. Per fork: step index, outcomes involved, factor deltas
between sibling group means at the fork step, and any `turning_points`/events recorded at
that step. If no divergence exists → empty list → map renders a single path.

**Alternative considered:** LLM-narrated branch reasons at render time. Rejected: results
pages must render with the LLM backend down (the devbox reality); LLM narrative is
*included when stored*, never required.

### D3: Fan chart = per-outcome mean + min/max band
Mean line per outcome with a min/max shaded band across member particles (not ±σ — min/max
is honest for small member counts and cheap to compute). Baseline = 50 dashed line (the
engine's factor baseline; `world_states` step 0 confirms it). Factor tabs via htmx swap of
a partial that renders a fresh SVG.

**Alternative considered:** per-particle spaghetti lines. Rejected for the primary view —
unreadable above ~10 lines; per-particle detail stays available through the debug tree and
outcome detail.

### D4: Futures map = weighted path diagram, not a literal graph layout
Render one trunk from "now", one visible path per outcome (thickness ∝ share), forks at
detected branch points. Ordering of paths (top→bottom) by probability. The literal 27-line
particle graph is *not* drawn — it is what made the old tree useless. Cornerstone markers
sit on forks; clicking opens a `<details>` rationale block (server-rendered, no JS beyond
htmx for path/story switching).

**Alternative considered:** a real force-directed particle graph (viz.js). Rejected:
violates the no-new-JS-deps constraint and re-creates the illegibility problem.

### D5: Story synthesis with explicit degradation tiers
Path story steps, in priority order: (1) stored `particle_states.events` for the
representative particle, (2) `projection_steps.summary` + `turning_points`, (3) factor
milestones derived deterministically from the artifact (crossings of baseline, largest
deltas, band edges). Tier used is recorded in the artifact (`story_source`) and shown in
the UI when it is not tier 1/2, so invented-looking text is never mistaken for recorded
events. End-state deltas come from the artifact; early-signals callout reuses
`outcomes.indicators`.

### D6: Scope-aware phrasing is a template-layer concern
The artifact stores neutral data. Phrasing ("you resign" vs "the subject exits
employment") happens in Jinja via a small scope phrasebook (verb/noun maps per scope), not
by storing two copies of text. Rationale: stored text stays scope-neutral and future
scopes (org, community) add phrasebook entries, not data migrations.

### D7: Heuristic-mode badge from existing budget data
Zero `llm_calls` rows for the job ⇒ heuristic mode badge in the overview strip. No new
tracking needed.

### D8: Tree page demotion, not deletion
`/results/<jid>/tree` stays (debug aid, tests reference it), removed from primary
navigation; the futures map becomes the structural view. Legacy-job healing keeps old jobs
renderable on the new page.

## Implementation Sketch

- `app/futures.py` (new, ~250 lines): artifact builder — aggregation, branch detection,
  milestone/story derivation, artifact read/write/heal. Pure functions, unit-testable
  without LLM.
- `app/routes/results.py`: futures view route renders from artifact; factor-tab and
  path-story htmx partial routes; SVG builders for fan chart and map (string-templated
  SVG like existing trajectory code); tree route kept.
- `app/templates/results/`: `index.html` reworked into overview strip + split + fan chart
  + cards + map + story; `partials/fan.html`, `partials/story.html`; CSS additions to
  `app/static`.
- `worker/workflows.py` / `app/steps/synthesis.py`: call the builder once at completion
  (wrapped in the existing `_write_retry` discipline; artifact failure must not fail the
  job).
- `app/present.py`: delta-chip and share-and-count formatting helpers.

## Risks / Trade-offs

- **Artifact staleness** if outcomes are re-clustered post-completion → artifact version
  field + builder keyed on job `updated_at`; stale artifacts are rebuilt on view.
- **Branch detection heuristics** may over/under-detect forks on odd particle graphs →
  single-path fallback is always valid; detection is unit-tested against synthetic
  lineages (fork, no-fork, re-merge).
- **SVG size** for long horizons → fine at 40 steps × ≤9 outcomes; axes decimate labels.

## Migration Plan

1. Ship builder + routes + templates behind the existing routes (same URLs).
2. Legacy jobs heal lazily on first view (mirrors `repair_legacy_outcomes`).
3. No data migration, no schema change; artifacts are additive.

## Open Questions

- None blocking. Map y-positioning for paths with equal shares is currently
  probability-ordered; can revisit during implementation.
