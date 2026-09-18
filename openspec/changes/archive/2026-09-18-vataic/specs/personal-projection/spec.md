## ADDED Requirements

### Requirement: Projection scopes
The system SHALL support at least four projection scopes — macro, meso, personal, and hybrid — recorded per job. Scope MUST be inferred from the premise or chosen explicitly by the user, and MUST affect factor selection, state construction, event generation, and outcome presentation.

#### Scenario: Scope inferred from premise
- **WHEN** a premise describes a global or systemic situation such as "House prices in Subang Jaya have plummeted"
- **THEN** the job is assigned the macro scope by default

#### Scenario: Explicit scope override
- **WHEN** a user selects the personal scope for a premise about a person's career change
- **THEN** the job runs a personal projection even if the premise contains environmental context

### Requirement: Subject card
For personal or hybrid projections, the system SHALL support an optional structured subject card containing known facts, goals, constraints, values, risk tolerance, agency assumptions, support system, and unknowns. When any of these fields are absent, the system MUST synthesize explicit assumptions, record them, and make them reviewable before projection begins. The subject card MUST be treated as private by default.

#### Scenario: Assumptions synthesized for missing fields
- **WHEN** a subject card is created without a risk tolerance value
- **THEN** the system records an explicit assumption such as "subject has moderate risk tolerance" and exposes it for review

#### Scenario: Privacy by default
- **WHEN** a personal projection job is created
- **THEN** its subject card and personal data are stored with a private privacy level and not listed publicly

### Requirement: Personal factor selection
Personal projections SHALL select consequential personal factors (from categories such as financial resilience, income trajectory, skill development, reputation, network access, emotional regulation, motivation, health and energy, family support, time availability, habit consistency, decision confidence, optionality, and environmental opportunity) combined with environment-context factors. Personal factor importance SHALL incorporate goal impact, wellbeing impact, financial impact, cascading effect, uncertainty, user salience, and reversibility. The system SHALL keep 10–18 consequential personal factors per personal job.

#### Scenario: Personal factor count
- **WHEN** factor selection runs for a personal projection
- **THEN** the retained factor count is within the configured personal limits even if more candidates were proposed

#### Scenario: Environment as context
- **WHEN** a personal projection is created
- **THEN** environment state factors are included as secondary context and excluded from the working factor set unless they become decisive

### Requirement: Agency and decision modeling
Personal projections SHALL model subject behavior through agency assumptions (baseline discipline, stress response, help-seeking, risk appetite, adaptability, persistence after failure). The engine MUST support behavioral feedback loops (e.g., small win → confidence → more action; rejection → avoidance → fewer opportunities) and MUST generate personal-scale event types: opportunity events, setback events, behavioral events, relationship events, and inner-state events.

#### Scenario: Behavioral feedback loop represented
- **WHEN** a causal graph is built for a personal projection
- **THEN** it can contain edges forming positive or negative behavioral loops over personal factors such as confidence, outreach, network access, and income

#### Scenario: Personal event branching
- **WHEN** the action context is a personal projection
- **THEN** candidate developments include subject decisions, reactions, relationship events, and inner-state changes, not only external events

### Requirement: Socio-emotional memory compression
Personal projections SHALL compress history into epoch summaries capturing emotional phases, behavioral patterns, relationship shifts, identity shifts, and key personal turning points, in addition to external events. Personal turning points (e.g., left job, first rejection, first client, burnout, runway warning) SHALL be pinned and retained in compressed form in permanent context.

#### Scenario: Personal epoch summary
- **WHEN** memory compression runs for a personal job at an epoch boundary
- **THEN** the produced summary covers emotional and behavioral patterns as well as factual events

#### Scenario: Turning points pinned
- **WHEN** a personal pivotal event occurs (e.g., a financial runway warning threshold is crossed)
- **THEN** the event is added to the pinned turning points list and remains visible in subsequent LLM contexts

### Requirement: Life-trajectory outcomes
Personal projections SHALL cluster final particles into life-trajectory outcomes evaluated across multiple wellbeing dimensions: financial stability, emotional wellbeing, goal attainment, relationship impact, health impact, identity alignment, skill growth, optionality, autonomy, and risk exposure. Each personal outcome card SHALL additionally include decision levers, protective actions, warning signs, support needed, reversibility notes, and wellbeing risks.

#### Scenario: Multi-dimensional outcome
- **WHEN** a personal outcome is finalized
- **THEN** it includes financial, wellbeing, relationship, and identity evaluations plus decision levers and warning signs

### Requirement: Personal stopping
Personal projection loops SHALL stop when any personal stopping condition is met: goal reached, goal abandoned, new equilibrium formed, crisis threshold crossed, decision window closed, financial stop-loss reached, emotional state stabilized, or user-specified horizon reached. A personal job MAY stop earlier than a macro job when its defining personal question has resolved.

#### Scenario: Financial stop-loss
- **WHEN** a personal projection's financial runway factor falls below the user-defined danger threshold
- **THEN** the loop stops and the outcome synthesis runs from the reached state

### Requirement: Data minimization and controls
Personal projections SHALL request only the personal data necessary for the scenario and SHALL expose user controls to review stored personal data, edit assumptions, exclude sensitive factors, export data, and delete personal data. The system MUST present results as structured possibilities and decision support, and MUST NOT present itself as therapy, medical, legal, or guaranteed financial advice.

#### Scenario: Delete personal data
- **WHEN** a user requests deletion of a personal projection job's subject data
- **THEN** the system removes the subject card, personal factors, and personal events from storage

#### Scenario: Ethical framing
- **WHEN** personal projection results are presented
- **THEN** the interface includes framing that results are structured scenario explorations for decision support, not predictions