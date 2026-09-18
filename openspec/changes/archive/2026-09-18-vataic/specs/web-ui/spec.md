## ADDED Requirements

### Requirement: Scenario submission
The web UI SHALL provide a form where a user enters a premise and optionally sets horizon, reality mode, scope, outcome count, detail level, budget limit, and perspective. On submission the UI SHALL create a job and communicate that the projection may take hours and that the browser may be closed.

#### Scenario: Job accepted with feedback
- **WHEN** a user submits a premise
- **THEN** the UI shows the job identifier and a notice that the projection runs asynchronously and may take a long time

#### Scenario: Optional settings included
- **WHEN** a user sets reality mode and horizon before submitting
- **THEN** those settings are attached to the created job

### Requirement: Jobs dashboard
The UI SHALL list all jobs with scenario text, current status, progress (e.g., current step of expected steps), last checkpoint time, and error state when failed. The dashboard MUST be server-rendered and MUST refresh via htmx without a full page reload.

#### Scenario: Running job shows progress
- **WHEN** a job is running
- **THEN** its dashboard row shows the live phase and step progress and is periodically refreshed via htmx

#### Scenario: Failed job offers retry
- **WHEN** a job is in the FAILED state
- **THEN** its dashboard row shows the failure reason and a retry action

### Requirement: Job detail and progress
The job detail page SHALL show current phase, latest checkpoint, error details, estimated completion, and budget usage. The page SHALL support controls to cancel, suspend, resume, and retry the job via htmx POST actions that return updated status fragments.

#### Scenario: Budget usage visible
- **WHEN** a running job page is open
- **THEN** the page displays LLM call count, token usage, and elapsed time from persisted budget state

### Requirement: Result explorer
After completion, the UI SHALL render a results view with outcome cards (name, probability band, summary, key factors, causal path, leading indicators, assumptions, risks), an expandable explanation per outcome, a timeline tree of branches, and factor trajectory views. Navigation between outcomes, branches, and trajectories MUST not require a full page load (htmx).

#### Scenario: Outcome cards rendered
- **WHEN** a completed job's results are requested
- **THEN** all outcomes are rendered as cards with probability bands and summaries

#### Scenario: Timeline tree interactive
- **WHEN** a user selects a branch node in the timeline tree
- **THEN** the view updates via htmx to show that branch's events, factor changes, and probability

### Requirement: Assumption review
Before the projection loop starts, the UI SHALL present the parsed scenario, selected consequential factors, weak-signal watchlist, causal graph summary, and recorded assumptions for user review and editing. Edits MUST be persisted to the job before the loop starts.

#### Scenario: Editable factor set
- **WHEN** a job is in the factor-selection phase and a user deselects a factor
- **THEN** the factor set for the job is updated and the projection proceeds without the deselected factor

### Requirement: Personal projection views
For personal-scope jobs, the UI SHALL provide a personal scenario builder (change, goal, constraints, resources, assumptions), privacy controls (private by default, sensitive-data warnings, export/delete), a personal outcome view, and a decision map of decision points encountered along the branches.

#### Scenario: Decision map shown
- **WHEN** a personal projection job completes
- **THEN** the results include a decision map listing the key decision points, alternatives, and the branches that followed each choice

#### Scenario: Data export
- **WHEN** a user requests an export of a personal job
- **THEN** the UI provides a downloadable artifact bundle with the job's persisted inputs, factors, outcomes, and report

### Requirement: Settings panel
The UI SHALL provide a settings view to configure the LLM provider (provider, base URL, model(s) per role, API key from server env), default projection budgets and thresholds, and deployment info. Changes MUST be reflected in subsequent jobs.

#### Scenario: Provider saved applies
- **WHEN** an administrator saves a new provider configuration
- **THEN** the next projection job's LLM calls use the saved configuration

### Requirement: Safety interstitial for personal premises
When a personal-scope premise involves self-harm, abuse, severe mental health crisis, or immediate danger, the UI SHALL pause the projection and present safety resources and a request to seek professional/emergency support rather than generating outcome projections.

#### Scenario: Crisis premise halted
- **WHEN** a user submits a personal-scope premise that the intake stage flags as a crisis topic
- **THEN** the job is halted with a safety message and resource links, and no outcome projection proceeds