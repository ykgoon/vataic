# Outcome Card Consolidation

## Why

A results page shows one card per future. Each card repeated the same ranked
factor list four times, under four labels:

- **Causal path** — "X rises steeply to a level far above where it started"
- **Watch for** — "X starting to surge"
- **Assumes** — "X stays up near this level rather than falling back"
- **What could break it** — "X falling back toward where it started would undo this path"

All four were generated from the same `factor_states`, strongest first, so a
card with four factors showed those four names twelve times in three verb
registers. The "Arc" paragraph above them restated the same factors a fourth
time, and the expanded "Read the full story" repeated the Arc's explanation
verbatim. Readers had to diff near-identical lists to find anything new.

## What Changes

- **One narrative section per card.** The four lists are replaced by a single
  "What to watch, and what would undo this path" list: one line per factor,
  carrying both its early signal and what would reverse it.
  - `app/narrate.py`: `reversal_verb()` and `watch_line()` build the line.
  - `app/present.py`: `watches_for()` is the shared builder for every view.
  - `app/futures.py`: each futures outcome carries a `watches` list
    (artifact version 4, so stored artifacts rebuild).
- **Arc is the card's narrative.** A real LLM explanation renders as the Arc.
  Without one, the machine summary is only shown when there are no chips to
  carry the same factor list.
- **The expanded story does not repeat the card.** `?hide_explanation=1` is set
  when the Arc already showed the explanation; the per-step "Depends on" block
  (the same outcome-level assumptions on every step) is removed.
- **Legacy views consolidated too:** the legacy card page and the
  `/results/<id>/outcome/<id>` detail fragment use the same single list.

No information is lost: every factor's direction, magnitude, early signal and
failure mode still appear, once each.

## Impact

- Affected specs: `web-ui` (result explorer)
- Affected code: `app/narrate.py`, `app/present.py`, `app/futures.py`,
  `app/routes/results.py`, `app/templates/results/{index,legacy}.html`,
  `app/templates/results/partials/{story,steps}.html`, `app/static/css/app.css`
- Data: `futures_view` artifacts bump to version 4 and rebuild on next view;
  no migration. Stored outcome rows keep their `causal_path`/`indicators`/
  `assumptions`/`risks` columns (still written by synthesis and read by
  nothing user-facing).
- Fixes three pre-existing bugs that blocked the suite: the `create_scenario`
  INSERT placeholder count, a stray `outcomes` reference in `cluster()`, and
  `sqlite3.Row.get` in the futures loader (plus a stale `baseline` assertion
  the crash had masked).
