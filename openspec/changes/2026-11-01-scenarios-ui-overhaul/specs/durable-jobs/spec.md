# Durable Jobs — Delta Spec

## REMOVED Requirements

### Requirement: Job lifecycle
Renamed to **Scenario lifecycle** (terminology change, requirement body preserved with updated wording; see ADDED below).

## ADDED Requirements

### Requirement: Scenario lifecycle
The system SHALL model each projection as a scenario with a persisted state machine containing at least the states: DRAFT, QUEUED, INITIALIZING, RUNNING, CHECKPOINTING, RECOVERING, CLUSTERING, EXPLAINING, COMPLETED, FAILED, CANCELLED, SUSPENDED. Transitions between states MUST be recorded with timestamps. A scenario SHALL only ever occupy one state, stored durably in the database.

#### Scenario: Scenario accepted
- **WHEN** a user submits a premise
- **THEN** a scenario is created and returned to the user with an identifier and a QUEUED or INITIALIZING status

#### Scenario: State transition recorded
- **WHEN** a scenario transitions from RUNNING to CLUSTERING
- **THEN** the transition is persisted with a timestamp and the scenario's current state is CLUSTERING

## MODIFIED Requirements

### Requirement: LLM call caching
The system SHALL store every LLM request and response persistently, keyed by a content hash of the request and the model and seed used. On resume or retry, a request with a matching cache key MUST reuse the stored response instead of re-invoking the provider. Before a queued scenario is picked up for execution, the system SHALL validate run readiness: the configured LLM provider MUST be reachable, and when the provider lists its models, the configured primary and strong models MUST be offered. A scenario whose provider is not ready SHALL transition to FAILED with the readiness error recorded, and MUST be re-runnable through the retry control once the provider is available. The validation MUST be skippable via the documented VATAIC_ALLOW_HEURISTIC escape hatch, which restores heuristic-fallback behavior for tests and emergencies.

#### Scenario: Cached response reused
- **WHEN** a resumed projection requests an LLM response whose (hash, model, seed) tuple already has a stored response
- **THEN** the stored response is reused without a new provider call and the event is recorded as a cache hit

#### Scenario: Unready provider fails the scenario at pickup
- **WHEN** a queued scenario is picked up while the configured provider is unreachable
- **THEN** the scenario transitions to FAILED with a recorded readiness error and the UI offers retry

#### Scenario: Ready provider allows execution
- **WHEN** a queued scenario is picked up while the provider is reachable and the configured models are served
- **THEN** the scenario proceeds through the pipeline normally

#### Scenario: Escape hatch restores heuristic mode
- **WHEN** VATAIC_ALLOW_HEURISTIC is set
- **THEN** readiness validation is skipped and projection proceeds with heuristic fallbacks

## REMOVED Requirements

### Requirement: Job controls
Renamed to **Scenario controls** (see ADDED below; deletion control added).

## ADDED Requirements

### Requirement: Scenario controls
The system SHALL expose controls to cancel, suspend, resume, retry, and delete a scenario, plus inspection of the latest checkpoint and the failed phase. Cancel and suspension MUST take effect at the next checkpoint boundary and MUST persist so the workflow halts cleanly. Deletion SHALL be limited to terminal scenarios (COMPLETED, FAILED, CANCELLED) and SHALL remove the scenario row, all child rows, and on-disk artifacts.

#### Scenario: Cancel at next boundary
- **WHEN** a scenario is cancelled while running
- **THEN** the workflow halts at the next checkpoint or cycle boundary and the scenario is recorded as CANCELLED

#### Scenario: Delete removes everything
- **WHEN** a user deletes a terminal scenario
- **THEN** the scenario row, all child rows, and on-disk artifacts are removed and the dashboard no longer lists it

#### Scenario: Delete blocked while active
- **WHEN** a user attempts to delete a scenario that is not in a terminal state
- **THEN** deletion is refused until the scenario is cancelled or completes

## MODIFIED Requirements

### Requirement: Final artifacts persisted
On completion, the system SHALL persist structured results (scenario, reality mode, factors, outcomes with probability and causal path, timeline tree, factor trajectories, warnings) and a human-readable report. All artifacts SHALL be permanently stored with the scenario and remain retrievable afterwards.

#### Scenario: Completed scenario retrievable
- **WHEN** a scenario has reached COMPLETED and the user later opens its page
- **THEN** the stored outcomes, timeline tree, factor trajectories, and report are rendered from persistent storage
