# Web UI — Delta Spec

## MODIFIED Requirements

### Requirement: Result explorer
After completion, the UI SHALL render a results view with outcome cards (name, probability band, narrative, key factor movements, and what to watch), an expandable full story per outcome, a futures map, and factor trajectory views. Navigation between outcomes, forks, and trajectories MUST not require a full page load (htmx).

Each outcome card SHALL present its factor evidence once: a ranked list of key factor movements and a single "What to watch, and what would undo this path" list giving, per factor, the early signal and the reversal that would unwind the path. A card MUST NOT repeat the same factor list under separate causal-path, leading-indicator, assumption, and risk headings. The card's narrative (Arc) MUST NOT be repeated inside its expanded story.

#### Scenario: Outcome cards rendered
- **WHEN** a completed scenario's results are requested
- **THEN** all outcomes are rendered as cards with probability bands, narratives, key factor movements, and one consolidated watch list

#### Scenario: No duplicated factor lists
- **WHEN** a card is rendered for an outcome whose factors moved
- **THEN** each moved factor appears once in the consolidated list with both its early signal and its reversal, and no separate causal-path/assumes/risks/leading-indicator sections are present

#### Scenario: Full story does not repeat the card
- **WHEN** a user expands "Read the full story" on a card that already shows an explanation as its Arc
- **THEN** the expanded story shows the time-anchored steps and rationale without re-printing that explanation

#### Scenario: No LLM explanation
- **WHEN** an outcome has no LLM explanation and its summary merely restates the factor list
- **THEN** the card shows the key-factor chips and the consolidated watch list without repeating that restatement as an Arc paragraph
