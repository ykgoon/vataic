## ADDED Requirements

### Requirement: Consequential factor selection
The system SHALL select a finite set of consequential factors for each job from domain-agnostic impact surfaces (e.g., physical environment, biology, population, resources, economy, governance, security, technology, information, culture, psychology, institutions, geography, time). Factors SHALL be scored for importance using a weighted formula of direct impact, cascading effect, uncertainty, policy/law relevance, and observability. The retained factor count MUST stay within a configurable limit (default 8–15; up to 20 for complex scenarios), with discarded candidates recorded in a weak-signal watchlist.

#### Scenario: Factors limited to configured maximum
- **WHEN** factor selection returns more candidates than the configured maximum
- **THEN** only the top-scoring factors are retained and the remainder are stored on a weak-signal watchlist

#### Scenario: Domain-agnostic selection
- **WHEN** a job is created for a non-economic premise such as "The internet fractures into national intranets"
- **THEN** selected factors are drawn from whatever impact surfaces apply to that premise rather than a fixed economic list

### Requirement: Factor typing
Each selected factor SHALL carry an identifier, a display name, a type (numeric index, continuous value, categorical, boolean, or event-based), a normalized value range, current value, baseline value, trend, momentum, volatility, and importance score. Factor values SHALL be normalized so different factors are comparable for distance and clustering computations.

#### Scenario: Factor record shape
- **WHEN** a factor is selected for a projection job
- **THEN** the stored factor record includes id, name, type, range, current value, baseline, trend, momentum, volatility, and importance score

### Requirement: Causal graph construction
The system SHALL build a causal graph over the selected factors, where edges carry a source factor, target factor, polarity, strength, delay, confidence, and optional conditions. The graph vertices MUST include only selected factors, and the graph MUST be validated for contradictions, impossible cycles, missing delays, and implausible strengths before use.

#### Scenario: Edge validated
- **WHEN** an edge is proposed where the source or target factor is not in the selected factor set
- **THEN** the edge is rejected during validation and recorded as a validation failure

#### Scenario: Contradictory cycle detected
- **WHEN** the proposed causal graph contains a self-contradictory cycle (a factor shown pushing an effect in opposite directions under identical conditions)
- **THEN** the graph is flagged and the critic step requests a revision before projection proceeds

### Requirement: Initial world state
The system SHALL construct an initial world state at time T0 with a value for every selected factor, sourced from known facts, retrieved knowledge, or recorded user assumptions. The initial state MUST be persisted and versioned with the job.

#### Scenario: Initial state persisted
- **WHEN** the initial world state is constructed
- **THEN** it is stored as the state at step index 0 and reused on resume

### Requirement: Adaptive time stepping
The system SHALL choose its own time step per projection cycle based on domain and volatility, bounded between a configurable minimum (default 1 week) and maximum (default 1 year). The engine MUST support an LLM-recommended next step value validated against the configured bounds.

#### Scenario: High volatility shrinks step
- **WHEN** the estimated volatility of the scenario state is high
- **THEN** the engine selects a smaller time step than the baseline for the next cycle

#### Scenario: Step within bounds
- **WHEN** a time step is selected or LLM-recommended
- **THEN** it is clamped so it never falls below the configured minimum or above the configured maximum

### Requirement: Projection cycle
Each projection cycle SHALL, for every active particle: build a compressed context, generate candidate developments through the LLM, validate the proposals through the critic/validation layer, apply causal effects to factor states, branch, merge, prune, and checkpoint the result. The cycle MUST record its progress as a completed step and MUST NOT lose completed work if interrupted after the step is saved.

#### Scenario: Step recorded atomically
- **WHEN** a projection step completes for all particles
- **THEN** the step index, time label, particle states, and summaries are persisted atomically and subsequent steps reference the persisted state

#### Scenario: Interrupted after step save
- **WHEN** a worker crashes after a step is persisted but before continuing
- **THEN** the job resumes from the next step index rather than recomputing the saved step

### Requirement: Branching control
The system SHALL branch a particle into child particles only when a development is probabilistically meaningful AND consequentially different, per configurable thresholds. A low-probability high-impact development MUST be retained as a tail-risk branch. The engine MUST NOT branch on low-probability low-impact developments.

#### Scenario: Tail risk retained
- **WHEN** a development has probability below the branch threshold but very high impact
- **THEN** a single tail-risk branch is retained for it

#### Scenario: Trivial development discarded
- **WHEN** a development has both low probability and low impact
- **THEN** no branch is created for it

### Requirement: Timeline population control
The system SHALL keep the active particle count within a configured beam width using a hybrid strategy: a configured number of slots for the highest-probability particles plus a reserved number of slots for high-impact divergent branches. The system SHALL merge particles whose normalized factor-state distance is below a merge threshold, assigning the merged particle the summed probability mass, and SHALL prune particles that are extremely low-probability, near-duplicates of a stronger branch, internally inconsistent, or stagnant unless representing a distinct equilibrium.

#### Scenario: Beam width respected
- **WHEN** the number of active particles exceeds the beam width after a cycle
- **THEN** the engine merges and prunes until the particle count is within the beam width

#### Scenario: Merge sums probability
- **WHEN** two particles are merged because their distance falls below the merge threshold
- **THEN** the merged particle carries the sum of the two particles' probability masses

### Requirement: Stopping rules
The projection loop SHALL stop when any configured stopping condition becomes true: user-specified horizon reached, budget cap reached (max steps, max LLM calls, max runtime/tokens), outcome clusters converged across checkpoints, divergence from baseline beyond threshold with stable clusters, or diminishing novelty (no meaningful new pivotal events for several consecutive cycles). The loop SHALL evaluate stopping conditions at every checkpoint.

#### Scenario: Horizon reached
- **WHEN** the projected time reaches the user-specified horizon
- **THEN** the projection loop ends and the outcome synthesis phase begins

#### Scenario: Convergence halts before horizon
- **WHEN** the top outcome clusters remain stable across two or three consecutive checkpoints
- **THEN** the loop ends early even if the horizon has not been reached

### Requirement: Budget enforcement
The system SHALL enforce hard budget caps on every job (max steps, max active branches, max LLM calls, max tokens, max runtime) and SHALL terminate a job with a COMPLETED or FAILED terminal state when a cap is exceeded rather than continuing indefinitely.

#### Scenario: LLM budget exhausted
- **WHEN** a job's LLM call budget is exhausted before projection completes
- **THEN** the job finalizes with the outcomes reached so far or transitions to FAILED with the budget reason recorded