# Futures Output Redesign

## Why

Vataic's promise is to give a sense of *possible futures* for a premise. The current
output does not deliver that promise legibly:

- The results page renders outcome cards with static factor scores ("Income Trajectory: 35")
  that carry no time, no direction, and no narrative arc — the reader cannot tell whether
  35 means "falling from 50" or "recovering from 20".
- The timeline tree page is an `<ol>` of particle hashes — a debug view of the particle
  store promoted to a user-facing page, with zero informational value for an end user.
- The job detail page is pipeline telemetry (budget bars, LLM call counters) — useful for
  operators, not for the person asking "what could happen?"

All the raw material for a legible output already exists in the database: per-step factor
trajectories for every particle (`particle_states`), clustered outcome narratives
(`outcomes`), step summaries and turning points (`projection_steps`), and real branch
lineage (`particles.parent_id`). The output is not rendered from it.

Approved direction is captured in the reviewed mockups `screens/mock1.png` (futures
overview) and `screens/mock2.png` (futures map + story).

## What Changes

- Replace the current results page with a single **futures view** organized around three
  questions asked in order: *what could happen?* (probability split + outcome cards),
  *how would it unfold?* (per-factor fan chart + path story), *when do paths split?*
  (futures map with cornerstones).
- Render the **futures map** — a left-to-right flow of the explored timeline population
  with branch points where timelines permanently diverged, path thickness proportional to
  share, and labeled forks — replacing the timeline tree as the primary structural view.
  The tree page is demoted to a debug route.
- Turn outcome cards into **futures, not scorecards**: probability shown as share of
  explored timelines with member counts ("62% · 15 of 27 timelines"), a short arc summary,
  end-state delta chips versus baseline, and the existing watch-for/assumptions/risks
  sections collapsed by default.
- Make every **cornerstone** — each branch point on the map and each pivotal step in a
  path story — click-to-expand with the **rationale** behind it: causal chain, factor
  deltas at that point, and assumptions the continuation depends on. Rationales come from
  stored data; deterministic when the job ran without LLM narrative.
- Persist a **versioned futures-view artifact** at job completion (per-outcome aggregate
  trajectories, member lists, end-state deltas, branch points) so the page never
  re-aggregates 27×40 particle states per request, with lazy healing for legacy jobs.
- Phrase outcomes and stories **scope-aware**: personal scope addresses the subject
  ("you"), macro scope describes the system in third person.
- Disclose **heuristic mode** (jobs that completed with zero LLM calls) so template-built
  narrative is not mistaken for LLM-written analysis.

## Capabilities

### New Capabilities
<!-- None: this change modifies existing capabilities. -->

### Modified Capabilities
- `web-ui`: the Result explorer requirement is replaced by the futures view (overview
  strip, probability split, factor fan chart, outcome cards, futures map, path story,
  cornerstone rationale disclosure, scope-aware phrasing). The timeline tree is demoted
  to a debug route.
- `outcome-synthesis`: new requirements to persist the futures-view artifact (per-outcome
  aggregate trajectories, member lists, end-state deltas) and to extract branch points
  from particle lineage as rationale inputs.

## Impact

- **Specs**: `openspec/specs/web-ui/spec.md`, `openspec/specs/outcome-synthesis/spec.md`
  (deltas in this change).
- **Backend (small)**: a futures-view builder module (deterministic, no LLM) invoked at
  the completion of the synthesis phase; artifact stored via the existing `artifacts`
  table. No DB schema migration required.
- **Frontend**: `app/templates/results/*` and result routes reworked; new server-rendered
  SVG builders (fan chart, futures map); static CSS additions. No JS framework, no client
  chart library — htmx + native `<details>` as per the current architecture.
- **Legacy data**: completed jobs predating the artifact are healed lazily on first view
  (same pattern as the existing legacy-outcome repair).
- **Removed from primary nav**: timeline tree link (route kept at `/results/<jid>/tree`
  as a debug view).
- **Non-goals**: multi-job comparison views; drag/zoom map interactivity; client-side
  rendering frameworks; any change to the projection engine or clustering itself.
