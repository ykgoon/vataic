# Outcome Synthesis Specification

## Purpose
Clustering of particles into outcome archetypes with explanations and uncertainty bands. TBD.

## Requirements

### Requirement: Outcome clustering
The system SHALL cluster the final set of timeline particles into a configurable number of outcome archetypes (default 5–9, user adjustable). Clustering SHALL combine numeric state-distance clustering with LLM-assisted semantic review and labeling. Each cluster SHALL record the probability weight of its member particles, and cluster weights SHALL be normalized to sum to 1 after clustering.

#### Scenario: Normalized outcome weights
- **WHEN** particle weights are aggregated into outcome clusters
- **THEN** the outcome probabilities are normalized so they sum to 1 across all outcomes

#### Scenario: Adjacent states merged into one archetype
- **WHEN** two particle clusters have near-identical representative states
- **THEN** they are merged into a single outcome archetype

### Requirement: Outcome content
Each final outcome SHALL contain: name, probability weight, summary narrative, key factor states, causal path (ordered pivotal events), leading indicators, assumptions and uncertainties, and risks. The outcome MUST be explainable from the stored causal traces of its member particles.

#### Scenario: Outcome card data present
- **WHEN** outcome synthesis completes
- **THEN** every outcome has a name, probability, summary, key factors, causal path, leading indicators, assumptions, and risks

### Requirement: Explanation generation
The system SHALL generate a user-facing explanation for each outcome that answers: what this future is, how likely it is, what caused it, which factors mattered most, what early signals would indicate it is emerging, what assumptions it requires, and what could invalidate it.

#### Scenario: Explanation covers causal chain
- **WHEN** a user expands an outcome card
- **THEN** the explanation describes the sequence of events and factor shifts that produced the outcome

### Requirement: Representative trace selection
When particles are merged or clustered, the system SHALL designate a representative particle per outcome and SHALL keep the alternative member particles' traces retrievable, so the outcome page can show the most representative story while acknowledging variations.

#### Scenario: Alternative paths inspectable
- **WHEN** a cluster contains multiple particles
- **THEN** the representative path is shown by default and the alternative particle paths remain accessible

### Requirement: Uncertainty presentation
The system SHALL present outcome probabilities in qualitative bands (e.g., dominant, plausible, alternative, tail risk) in addition to numeric weights, and SHALL display a disclaimer that projections are structured scenario explorations, not predictions.

#### Scenario: Bands and disclaimer shown
- **WHEN** outcomes are presented on the results page
- **THEN** each outcome displays a probability band and the page includes the scenario-exploration disclaimer
