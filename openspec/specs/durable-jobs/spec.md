# Durable Jobs Specification

## Purpose
Durable execution of projection jobs with state machine, checkpoints, recovery, and persisted artifacts. TBD.

## Requirements

### Requirement: Job lifecycle
The system SHALL model each projection as a job with a persisted state machine containing at least the states: DRAFT, QUEUED, INITIALIZING, RUNNING, CHECKPOINTING, RECOVERING, CLUSTERING, EXPLAINING, COMPLETED, FAILED, CANCELLED, SUSPENDED. Transitions between states MUST be recorded with timestamps. A job SHALL only ever occupy one state, stored durably in the database.

#### Scenario: Job accepted
- **WHEN** a user submits a scenario
- **THEN** a job is created and returned to the user with an identifier and a QUEUED or INITIALIZING status

#### Scenario: State transition recorded
- **WHEN** a job transitions from RUNNING to CLUSTERING
- **THEN** the transition is persisted with a timestamp and the job's current state is CLUSTERING

### Requirement: Durable workflow execution
The system SHALL execute projection jobs via an in-process projection worker (`ProjectionJobWorkflow`) whose activities are idempotent and retried safely. Each activity MUST write its result to persistent storage before the workflow advances. The workflow MUST support resuming from the last committed checkpoint after worker crash, machine shutdown, or redeployment.

#### Scenario: Activity retry is safe
- **WHEN** a workflow activity fails transiently and is retried
- **THEN** the retry does not produce duplicate effects because the operation is idempotent with respect to its idempotency key

#### Scenario: Resume from checkpoint
- **WHEN** a workflow resumes after an interruption in the projection loop at step 17
- **THEN** it resumes at step 17 using the saved checkpoint and does not re-run steps 0-16

### Requirement: Checkpointing
The system SHALL persist a durable checkpoint after every projection step (or no less often than a configurable interval, default 15–30 minutes for very large jobs) containing phase, step index, particle states, compressed memory, causal graph version, RNG seed, and budget state. Each checkpoint SHALL be immutable once written and recoverable.

#### Scenario: Per-step checkpoint
- **WHEN** a projection step completes
- **THEN** a checkpoint is persisted that is sufficient to resume the job at the following step without losing LLM work already paid for

### Requirement: Idempotency keys
The system SHALL assign deterministic idempotency keys to every mutating operation (scenario parse, factor selection, causal graph build, projection step, particle transition, LLM call). Before executing an operation, the system SHALL check whether its key already has a stored result and reuse it if present.

#### Scenario: Replayed transition reuses result
- **WHEN** a particle transition is replayed because a worker died after saving the transition result
- **THEN** the stored transition result is reused and no duplicate child particles or LLM calls are created

### Requirement: LLM call caching
The system SHALL store every LLM request and response persistently, keyed by a content hash of the request and the model and seed used. On resume or retry, a request with a matching cache key MUST reuse the stored response instead of re-invoking the provider.

#### Scenario: Cached response reused
- **WHEN** a resumed job needs the LLM response for a (phase, step, particle, context-hash, model, seed) tuple that was already produced
- **THEN** the stored response is reused without a new provider call and the event is recorded as a cache hit

### Requirement: Leases and heartbeats
The worker processing a job SHALL hold a lease with a heartbeat. A lease MUST expire if not renewed within its duration, after which any worker MAY take over the job. A job marked RUNNING whose lease has expired SHALL be marked RECOVERING and resumed from its latest checkpoint.

#### Scenario: Taking over an expired lease
- **WHEN** a worker dies and its lease expires
- **THEN** another worker may acquire the job's lease and resume it from the latest checkpoint, first recording the job as RECOVERING

### Requirement: Recovery procedure
On startup and periodically, the system SHALL scan for jobs marked RUNNING or RECOVERING without a valid lease, load their latest checkpoint, validate its integrity, and resume from the checkpointed phase. The recovery path MUST be automatic and not require manual intervention.

#### Scenario: Automatic recovery on worker start
- **WHEN** a worker process starts and finds an expired-lease job at phase projection_loop step 17
- **THEN** it recovers the job and continues from step 17

### Requirement: Budget tracking
The system SHALL track per-job budget usage (LLM calls, tokens, compute seconds, elapsed time) durably and SHALL consult the budget at every checkpoint to decide between continuing, finalizing, or failing. Budget state SHALL be included in checkpoints so budget enforcement survives restart.

#### Scenario: Budget state restored
- **WHEN** a job resumes after a crash in the middle of the projection loop
- **THEN** the budget counters restored from the last checkpoint are respected in subsequent budget checks

### Requirement: Job controls
The system SHALL expose controls to cancel, suspend, resume, and retry a job, plus inspection of the latest checkpoint and the failed phase. Cancel and suspension MUST take effect at the next checkpoint boundary and MUST persist so the workflow halts cleanly.

#### Scenario: Cancel at next boundary
- **WHEN** a job is cancelled while running
- **THEN** the workflow halts at the next checkpoint or step boundary and the job is recorded as CANCELLED

### Requirement: Final artifacts persisted
On completion, the system SHALL persist structured results (scenario, reality mode, factors, outcomes with probability and causal path, timeline tree, factor trajectories, warnings) and a human-readable report. All artifacts SHALL be permanently stored with the job and remain retrievable afterwards.

#### Scenario: Completed job retrievable
- **WHEN** a job has reached COMPLETED and the user later opens its page
- **THEN** the stored outcomes, timeline tree, factor trajectories, and report are rendered from persistent storage
