# Web UI — Delta Spec

## REMOVED Requirements

### Requirement: Jobs dashboard
Renamed to **Scenarios dashboard** (see ADDED below; navigation slimming and delete affordance added).

## ADDED Requirements

### Requirement: Scenarios dashboard
The UI SHALL list all scenarios with premise text, current status, progress (current cycle of expected cycles), last checkpoint time, and error state when failed. The dashboard MUST be server-rendered and MUST refresh via htmx without a full page reload. The primary navigation SHALL contain Scenarios, New Scenario, and Settings; the personal page SHALL NOT be listed in primary navigation while its routes remain reachable. Each dashboard row SHALL offer deletion of terminal scenarios behind a confirmation.

#### Scenario: Running scenario shows progress
- **WHEN** a scenario is running
- **THEN** its dashboard row shows the live phase and cycle progress and is periodically refreshed via htmx

#### Scenario: Failed scenario offers retry
- **WHEN** a scenario is in the FAILED state
- **THEN** its dashboard row shows the failure reason and a retry action

#### Scenario: Terminal scenario can be deleted
- **WHEN** a user activates delete on a COMPLETED, FAILED, or CANCELLED scenario row and confirms
- **THEN** the row disappears from the dashboard and the scenario is removed

#### Scenario: Personal page not in primary navigation
- **WHEN** the user views the site header
- **THEN** no navigation link targets the personal page, and /personal remains accessible by URL and from personal-scoped scenarios

## MODIFIED Requirements

### Requirement: Scenario submission
The web UI SHALL provide a form where a user enters a premise and optionally sets horizon, reality mode, scope, outcome count, detail level, budget limit, and perspective. On submission the UI SHALL create a scenario and communicate that the projection may take hours and that the browser may be closed.

#### Scenario: Scenario accepted with feedback
- **WHEN** a user submits a premise
- **THEN** the UI shows the scenario identifier and a notice that the projection runs asynchronously and may take a long time

#### Scenario: Optional settings included
- **WHEN** a user sets reality mode and horizon before submitting
- **THEN** those settings are attached to the created scenario

## REMOVED Requirements

### Requirement: Job detail and progress
Renamed to **Scenario detail and progress** (see ADDED below; chat panel entry point added).

## ADDED Requirements

### Requirement: Scenario detail and progress
The scenario detail page SHALL show current phase, latest checkpoint, error details, estimated completion, and budget usage. The page SHALL support controls to cancel, suspend, resume, retry, and delete the scenario via htmx POST actions that return updated status fragments. The page SHALL include the scenario conversation panel (see scenario-chat).

#### Scenario: Budget usage visible
- **WHEN** a running scenario page is open
- **THEN** the page displays LLM call count, token usage, and elapsed time from persisted budget state

## MODIFIED Requirements

### Requirement: Assumption review
Before the projection loop starts, the UI SHALL present the parsed scenario, selected consequential factors, weak-signal watchlist, causal graph summary, and recorded assumptions for user review and editing. Edits MUST be persisted to the scenario before the loop starts.

#### Scenario: Editable factor set
- **WHEN** a scenario is in the factor-selection phase and a user deselects a factor
- **THEN** the factor set for the scenario is updated and the projection proceeds without the deselected factor

### Requirement: Personal projection views
For personal-scope scenarios, the UI SHALL provide a personal scenario builder (change, goal, constraints, resources, assumptions), privacy controls (private by default, sensitive-data warnings, export/delete), a personal outcome view, and a decision map of decision points encountered along the branches. The personal page SHALL be reachable from personal-scoped scenarios and by URL, but SHALL NOT appear in primary navigation.

#### Scenario: Decision map shown
- **WHEN** a personal projection completes
- **THEN** the results include a decision map listing the key decision points, alternatives, and the branches that followed each choice

#### Scenario: Data export
- **WHEN** a user requests an export of a personal scenario
- **THEN** the UI provides a downloadable artifact bundle with the scenario's persisted inputs, factors, outcomes, and report
