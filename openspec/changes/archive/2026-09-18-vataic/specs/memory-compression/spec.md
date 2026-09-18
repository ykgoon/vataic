## ADDED Requirements

### Requirement: Three-tier memory model
The system SHALL maintain per-particle memory in three tiers: an immutable scenario card (premise, scope, reality mode, assumptions, horizon), a current state snapshot (time label plus all factor values), and rolling epoch summaries. The LLM context for a projection step MUST be composed from these tiers plus pinned turning points and a causal graph digest, and MUST NOT contain the full raw event history.

#### Scenario: LLM context excludes raw history
- **WHEN** a compressed context is built for a step late in a long projection
- **THEN** the context contains the scenario card, current factor state, recent epoch summary, pinned turning points, and causal graph digest, but not the full raw event log

### Requirement: Epoch summarization
The system SHALL compress recent history into epoch summaries at configurable intervals (e.g., every four quarters for macro, every few months for personal). Each epoch summary SHALL be produced by an LLM call that is cached and idempotent, and the summarized raw events MUST remain stored in the database even though they are excluded from live context.

#### Scenario: Epoch summary replaces raw events
- **WHEN** a new epoch summary has been produced
- **THEN** subsequent step contexts reference the summary instead of the raw events of that epoch

#### Scenario: Raw log retained
- **WHEN** an epoch is summarized
- **THEN** the raw events remain persisted in storage and are not deleted

### Requirement: Pinned turning points
The system SHALL maintain a per-particle pinned turning-points list to which pivotal events are added (e.g., initial crash, major policy change, developer bailout, burnout episode, financial runway warning). Pinned turning points SHALL be retained in compressed form in every subsequent context and MUST NOT be summarized away.

#### Scenario: Turning point persists in context
- **WHEN** a pivotal event is pinned at an early step
- **THEN** it appears in compressed form in every later step context until the projection ends

### Requirement: Compressed context object
Each LLM projection-step call SHALL receive a compact JSON context with exactly these sections: scenario card, current time, factor state, pinned turning points, recent epoch summary, causal graph digest, and a narrow task instruction. The context MUST fit within a configured token budget, truncating only the least important tier (epoch summaries) when over budget.

#### Scenario: Context within token budget
- **WHEN** a context object is composed and exceeds the configured token budget
- **THEN** epoch summary detail is reduced until the context fits, while the scenario card, turning points, and factor state remain intact

### Requirement: Memory durability
All memory tiers and their versions SHALL be stored persistently per job (and per particle where applicable) so a resumed job reconstructs an identical compressed context to the one that existed before the interruption.

#### Scenario: Context reproducible after resume
- **WHEN** a job resumes from a checkpoint
- **THEN** the compressed context for the next step is reconstructed identically from persisted tier data and cached LLM calls are reusable