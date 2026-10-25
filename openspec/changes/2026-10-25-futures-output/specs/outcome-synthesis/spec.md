# Futures Output Redesign — Delta Spec: outcome-synthesis

## ADDED Requirements

### Requirement: Futures-view artifact
On job completion, the system SHALL persist a versioned futures-view artifact containing,
per outcome: aggregate trajectories (mean, minimum, and maximum per active factor at each
step across the outcome's member particles), the member particle identifiers with their
weights, end-state deltas versus the baseline, and the detected branch-point list. The
artifact SHALL be stored through the existing artifacts mechanism and SHALL be optional
for job execution: a missing artifact MUST NOT fail a job. Completed jobs that predate
the artifact SHALL be healed lazily — the artifact is computed on first view and
persisted.

#### Scenario: Artifact written at completion
- **WHEN** a job reaches the COMPLETED state
- **THEN** a versioned futures-view artifact exists with per-outcome trajectories, member lists, end-state deltas, and branch points

#### Scenario: Legacy job healed on first view
- **WHEN** a completed job has no futures-view artifact and its results are requested
- **THEN** the artifact is computed deterministically from stored particle data, persisted, and used to render the page

### Requirement: Branch-point extraction
The system SHALL detect branch points from particle lineage (`parent_id`): a branch point
is a step at which active timelines split such that their descendants end in different
outcomes, or at which an outcome's representative lineage diverges from sibling
timelines. Each branch point SHALL record the step index, the outcomes involved, the
per-factor state deltas at the fork, and any stored events or turning points at that
step, as rationale inputs for the futures view. Jobs whose particles never diverge SHALL
yield an empty branch-point list.

#### Scenario: Permanent divergence detected
- **WHEN** sibling particles split at a step and their descendants end in different outcomes
- **THEN** a branch point is recorded with the step index, the outcomes involved, and the factor deltas at the fork

#### Scenario: Fork without stored events still yields rationale inputs
- **WHEN** a branch point has no stored events or turning points at its step
- **THEN** the branch point is still recorded with its factor deltas so the UI can render a data-derived rationale

#### Scenario: No divergence
- **WHEN** a job's particle graph has no divergence
- **THEN** the branch-point list is empty and the futures view renders a single path
