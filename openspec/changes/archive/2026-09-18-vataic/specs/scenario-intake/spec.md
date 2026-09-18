## ADDED Requirements

### Requirement: Premise ingestion
The system SHALL accept an arbitrary free-text user premise and store it as the raw input of a projection job. The system MUST NOT reject a premise based on domain (economic, social, physical, fictional, speculative). Acceptance of the premise as the starting axiom MUST NOT be debated by downstream stages.

#### Scenario: Premise submitted
- **WHEN** a user submits the premise "House prices in Subang Jaya have plummeted"
- **THEN** the system creates a projection job, stores the raw premise, and returns a job identifier

#### Scenario: Any-domain premise accepted
- **WHEN** a user submits a fantastical or speculative premise such as "Gravity weakens by 5%" or "A major religion produces verifiable proof of an afterlife"
- **THEN** the system accepts the premise and records it as the starting axiom without rejection

### Requirement: Scenario structuration
The system SHALL parse a premise into structured scenario data including premise type, axioms, affected entities, affected systems, reality mode, and time horizon. When elements cannot be determined with confidence, the system MUST apply documented defaults and record the assumptions it made.

#### Scenario: Structured parse with defaults
- **WHEN** the system parses a premise and cannot determine a time horizon
- **THEN** it sets horizon to "auto", records the assumption, and proceeds

#### Scenario: Reuse of parsed scenario
- **WHEN** a job resumes from a checkpoint after the parsing phase
- **THEN** the stored parsed scenario is reused without re-running the LLM parse

### Requirement: Reality modes
The system SHALL support at least three reality modes — grounded, speculative, and fantastical — and SHALL record the selected mode on the job. The chosen mode MUST constrain downstream plausibility checking, where grounded applies strong real-world constraints and fantastical emphasizes internal consistency over present-day realism.

#### Scenario: Grounded mode
- **WHEN** a job runs in grounded mode
- **THEN** proposed developments that contradict known real-world constraints are discarded or flagged during validation

#### Scenario: Speculative premise
- **WHEN** a user provides a speculative premise and the job runs in speculative mode
- **THEN** the premise's core impossibility is accepted as an axiom while consequential deduction remains realistic

### Requirement: Ambiguity handling
The system SHALL either proceed with sensible defaults for ambiguous scenario fields or surface a clarifying question, and SHALL record any applied assumption so it can be reviewed and edited before projection begins.

#### Scenario: Assumption review prior to projection
- **WHEN** a job reaches the factor selection phase with recorded assumptions from parsing
- **THEN** the assumptions are available for review and editing by the user before the projection loop begins