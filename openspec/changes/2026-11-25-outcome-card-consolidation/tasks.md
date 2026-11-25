# Outcome Card Consolidation — Tasks

## 1. Narrative consolidation

- [x] 1.1 `narrate.watch_line` / `reversal_verb`: one watch-and-unwind line per factor
- [x] 1.2 `present.watches_for` shared builder; `prepare_outcomes` exposes `watches`
- [x] 1.3 `futures.py` outcome `watches` list; artifact version 4
- [x] 1.4 Cards (`index.html`), legacy cards and the outcome-detail fragment use the single list
- [x] 1.5 Story panel shows the consolidated block; per-step "Depends on" removed

## 2. Arc / full-story de-duplication

- [x] 2.1 Arc renders the LLM explanation; machine summary only when no chips exist
- [x] 2.2 `hide_explanation=1` when the card already showed the explanation

## 3. Verification

- [x] 3.1 Regression test: no "Watch for"/"Assumes"/"What could break it"/"Causal path" in cards
- [x] 3.2 Full suite green (58 passed)
- [x] 3.3 Rendered the page with production-shaped data: four lists gone, chips and watch lines agree
