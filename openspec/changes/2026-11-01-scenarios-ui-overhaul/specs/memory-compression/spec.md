# Memory Compression — Delta Spec (terminology)

## MODIFIED Requirements

### Requirement: Memory durability
All memory tiers and their versions SHALL be stored persistently per scenario (and per particle where applicable) so a resumed scenario reconstructs an identical compressed context to the one that existed before the interruption.

#### Scenario: Context reproducible after resume
- **WHEN** a scenario resumes from a checkpoint
- **THEN** the compressed context for the next step is reconstructed identically from persisted tier data and cached LLM calls are reusable
