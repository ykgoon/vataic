# Futures Output Redesign — Delta Spec: web-ui

## MODIFIED Requirements

### Requirement: Result explorer
After completion, the UI SHALL render a single futures view organized as: an overview
strip, a probability split of the explored timelines, a per-factor trajectory fan chart,
outcome cards, and a futures map with a selectable path story. Navigation between factor
views, path stories, and cornerstone rationales MUST NOT require a full page load (htmx
swaps or in-page disclosure). The legacy timeline tree page SHALL be excluded from
primary navigation and remain reachable only as a debug view.

#### Scenario: Futures view renders after completion
- **WHEN** a completed job's results are requested
- **THEN** the page renders the overview strip, probability split, fan chart, outcome cards, and futures map without error

#### Scenario: Timeline tree demoted
- **WHEN** a user browses the results navigation
- **THEN** no primary navigation link targets the timeline tree, and the tree remains accessible at its debug route

## ADDED Requirements

### Requirement: Futures overview strip
The futures view SHALL open with an overview strip showing the premise, the horizon
label, the number of timelines explored, the number of distinct outcome futures, and —
when the job completed with zero LLM calls — a visible heuristic-mode badge indicating
that narrative text is template-generated.

#### Scenario: Completed job summary at a glance
- **WHEN** a completed job's futures view is opened
- **THEN** the strip shows the premise, horizon, timeline count, and distinct-future count

#### Scenario: Heuristic run is disclosed
- **WHEN** a completed job recorded zero LLM calls
- **THEN** the strip shows a heuristic-mode badge

### Requirement: Probability split
The futures view SHALL render each outcome's probability as its share of the explored
timelines together with the member timeline count (e.g. "62% · 15 of 27 timelines"),
ordered from most to least probable, with band colors used consistently across the split,
fan chart, cards, and map.

#### Scenario: Split rendered
- **WHEN** the futures view is rendered
- **THEN** every outcome appears once in the split with its share and member count, sorted by probability descending

### Requirement: Factor fan chart
The futures view SHALL provide a per-factor trajectory chart rendering one line per
outcome — the mean of its member particles' values at each step — with a shaded band
spanning the minimum and maximum across member particles, the baseline value drawn as a
dashed reference line, and axis labels using human time labels when stored, falling back
to step indices otherwise. Selecting a different factor MUST update the chart without a
full page load.

#### Scenario: Trajectories over time
- **WHEN** a factor is selected
- **THEN** the chart shows one mean line and spread band per outcome over the full horizon with the baseline reference

#### Scenario: Factor switch without reload
- **WHEN** a user selects another factor tab
- **THEN** the chart updates via htmx without a full page load

#### Scenario: No stored time labels
- **WHEN** the job has no human time labels for its steps
- **THEN** the axis labels fall back to step indices

### Requirement: Outcome cards as futures
Outcome cards SHALL present: the outcome name, its band, its probability share with
member count, a short arc summary describing how the future unfolds over time, end-state
deltas versus baseline for key factors with direction indicators, and collapsed sections
for watch-for signals, assumptions, risks, and the causal path that expand in place.

#### Scenario: Card shows the future, not a scorecard
- **WHEN** outcome cards are rendered
- **THEN** each card shows share-and-count probability, an arc summary, and end-state delta chips, with detail sections collapsed until expanded

### Requirement: Futures map
The futures view SHALL include a left-to-right futures map of the explored timeline
population: a common origin at the start of the horizon, branch points where timelines
permanently diverged, one visible path per outcome colored by its band with thickness
proportional to its share of timelines, and a label at each fork naming what happened.
Selecting a path MUST update the path story without a full page load. When the particle
graph contains no forks, the map SHALL render a single path.

#### Scenario: Fork labeled and weighted
- **WHEN** timelines split permanently at a step
- **THEN** the map shows a branch point at that step with a fork label, and each outgoing path's thickness reflects its share of timelines

#### Scenario: Path selects its story
- **WHEN** a user selects a path on the map
- **THEN** the path story below updates via htmx to that outcome

#### Scenario: Single-path job
- **WHEN** the job explored a single timeline with no divergence
- **THEN** the map renders one path with no branch points

### Requirement: Path story
The futures view SHALL render a numbered, time-anchored story for the selected outcome
(most probable by default): key steps with time labels, the events and factor movements
that define the arc, an end-state summary, and an early-signals callout drawn from the
outcome's leading indicators. Story steps SHALL be built from stored step summaries and
particle events; when these are absent, steps SHALL be synthesized from factor milestones
(e.g. "income crosses baseline around step 8") without inventing events.

#### Scenario: Story of the dominant path
- **WHEN** the futures view is opened
- **THEN** the story of the most probable outcome is shown with time-anchored steps and an early-signals callout

#### Scenario: Heuristic fallback for story steps
- **WHEN** a job has no stored step summaries or particle events
- **THEN** story steps are derived from factor milestones and the page does not present invented events

### Requirement: Cornerstone rationale disclosure
Every cornerstone — each branch point on the futures map and each pivotal step in a path
story — SHALL be click-to-expand and SHALL reveal the rationale behind it: the causal
chain leading to it, which factors moved at that point and by how much, and which
assumptions the continuation depends on. Rationale content SHALL be built from stored
data (events, turning points, causal paths, factor deltas). When LLM narrative is
available for the cornerstone it SHALL be included; when it is not, the disclosure SHALL
present the deterministic data-derived rationale instead of being empty. Rationales MUST
be hidden until expanded and MUST NOT require a full page load to reveal.

#### Scenario: Branch rationale expands on click
- **WHEN** a user clicks a branch point on the futures map
- **THEN** an in-place disclosure opens with the causal chain, factor deltas at the fork, and dependent assumptions

#### Scenario: Pivotal step rationale expands on click
- **WHEN** a user clicks a pivotal step in a path story
- **THEN** an in-place disclosure opens with that step's rationale

#### Scenario: Rationale without LLM narrative
- **WHEN** a cornerstone has no stored LLM narrative
- **THEN** the disclosure shows the data-derived rationale (factor deltas, events, turning points) and is not empty

### Requirement: Scope-aware phrasing
Outcome names, arc summaries, and story text SHALL adapt to job scope: personal-scope
jobs SHALL address the subject in second person, macro-scope jobs SHALL describe the
system in third person. Both scopes MUST be served from the same stored data and page
structure, differing only in phrasing.

#### Scenario: Personal scope addresses the subject
- **WHEN** a personal-scope job's futures view is rendered
- **THEN** outcome and story phrasing addresses the subject as "you"

#### Scenario: Macro scope describes the system
- **WHEN** a macro-scope job's futures view is rendered
- **THEN** outcome and story phrasing describes the system in third person
