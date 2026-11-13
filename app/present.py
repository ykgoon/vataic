"""Presentation helpers shared by scenario-detail and results views: human
probability strings, factor-name mapping, parsed JSON lists, outcome rows.

Also heals legacy outcome rows (pre-readability pipeline) lazily on first
view, so completed scenarios run before the fix still render sensibly.
"""
import json
import re

from app.db import get_conn

_LEGACY_NAME_RE = re.compile(r"^(f\d+ \d+)( / f\d+ \d+)*$")
_repaired: set[str] = set()


def pct(x) -> str:
    """Human probability string (e.g. '3.1%'), never raw float noise."""
    try:
        v = float(x) * 100.0
    except Exception:
        return "?"
    if v >= 10:
        return f"{v:.0f}%"
    if v >= 1:
        return f"{v:.1f}%"
    if v >= 0.1:
        return f"{v:.2f}%"
    return "<0.1%"


def as_list(value, limit: int = 6) -> list[str]:
    """Parse a stored JSON array (or newline/comma string) into display lines."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        items = value
    else:
        try:
            items = json.loads(value)
        except Exception:
            import re as _re
            items = [s.strip() for s in _re.split(r"[\n;]|, ", str(value)) if s.strip()]
    if not isinstance(items, list):
        items = [items]
    return [str(x) for x in items][:limit]


def factor_names_for_scenario(jid: str, db_path=None) -> dict:
    """Map factor id -> human name for a scenario's selected factor set."""
    with get_conn(db_path) as c:
        r = c.execute(
            "SELECT factors FROM factor_sets WHERE scenario_id=? ORDER BY id DESC LIMIT 1",
            (jid,)).fetchone()
    names: dict[str, str] = {}
    if r:
        try:
            for f in json.loads(r["factors"]):
                if isinstance(f, dict) and f.get("id"):
                    names[f["id"]] = (f.get("name") or f["id"]).strip()
        except Exception:
            pass
    return names


def movers(fs, names: dict, limit: int = 5) -> list[tuple[str, float]]:
    """Factors furthest from baseline as (human label, value)."""
    if isinstance(fs, str):
        try:
            fs = json.loads(fs)
        except Exception:
            fs = {}
    if not isinstance(fs, dict):
        fs = {}
    items = []
    for k, v in fs.items():
        try:
            v = float(v)
        except Exception:
            continue
        if abs(v - 50.0) >= 3.0:
            items.append((names.get(k, k), v))
    items.sort(key=lambda t: abs(t[1] - 50.0), reverse=True)
    return items[:limit]


def prepare_outcomes(outs: list[dict], jid: str, db_path=None) -> list[dict]:
    """Convert raw outcome rows into template-ready dicts."""
    names = factor_names_for_scenario(jid, db_path)
    rows = []
    for o in outs:
        try:
            fs = json.loads(o.get("factor_states") or "{}")
        except Exception:
            fs = {}
        rows.append({
            "id": o.get("id"),
            "name": o.get("name") or "Outcome",
            "probability": o.get("probability") or 0.0,
            "prob_display": pct(o.get("probability") or 0.0),
            "band": o.get("band") or "tail",
            "summary": o.get("summary") or "",
            "explanation": o.get("explanation") or "",
            "movers": movers(fs, names),
            "indicators": as_list(o.get("indicators")),
            "assumptions": as_list(o.get("assumptions")),
            "risks": as_list(o.get("risks")),
            "causal_path": as_list(o.get("causal_path"), limit=8),
        })
    return rows


# Text written by older builds describes factor movement with the storage
# format: "shifted from 50 to 35", "drifting to 35/100", "above baseline",
# "snapping back to baseline", "ends at 78/100", "+12 points". A reader has no
# anchor for any of it, so such rows are regenerated from their stored factor
# states through app.narrate.
def _prose_leak(text: str) -> bool:
    from app.narrate import prose_has_index
    return prose_has_index(text)


_NUMERIC_PHRASING_RE = re.compile(
    r"/100|shifted from|drifting to|snapping back|\bbaseline\b|"
    r"ends at \d|out of 100|\b\d+ points\b|0=low", re.IGNORECASE)
# The deterministic narrative is the only writer that appends this line, so
# its presence marks text we can safely regenerate ourselves.
_HEURISTIC_MARKER = "These are structured scenario explorations, not predictions."


# _summarize() in the synthesis step is the only writer that ends a summary
# with one of these clauses, so their presence proves we can rebuild it.
_MACHINE_SUMMARY_MARKERS = ("evolving timeline(s) of the premise",
                            "timeline(s) explored)")


def _machine_summary(summary: str) -> bool:
    return any(m in (summary or "") for m in _MACHINE_SUMMARY_MARKERS)


def _needs_renarration(o: dict) -> bool:
    for field in ("causal_path", "indicators", "assumptions", "risks"):
        if _NUMERIC_PHRASING_RE.search(o.get(field) or ""):
            return True
    summary = o.get("summary") or ""
    if _NUMERIC_PHRASING_RE.search(summary) and _machine_summary(summary):
        return True
    expl = o.get("explanation") or ""
    return bool(_NUMERIC_PHRASING_RE.search(expl)) and _HEURISTIC_MARKER in expl


def renarrate_outcomes(jid: str, db_path=None) -> int:
    """Rewrite stored outcome text that still speaks in index numbers.

    Two passes, both deterministic, no LLM:

    * the factor-derived lists (causal path, indicators, assumptions, risks)
      are regenerated from factor_states, and a summary is rebuilt when it
      carries the machine-generated marker;
    * free prose (a summary or explanation written by an older LLM pass) is
      kept but has its quoted index values rewritten as words, so a good
      sentence is not thrown away over a number.

    Returns the number of outcomes changed.
    """
    from app.narrate import sanitize_prose
    from app.steps.synthesis import _deterministic_enrich, _narrative, _summarize
    names = factor_names_for_scenario(jid, db_path)
    with get_conn(db_path) as c:
        outs = [dict(r) for r in c.execute(
            "SELECT id,probability,band,summary,factor_states,causal_path,indicators,"
            "assumptions,risks,explanation FROM outcomes WHERE scenario_id=?", (jid,))]
    fixed = 0
    for o in outs:
        if not _needs_renarration(o):
            # Prose-only leak (e.g. an LLM explanation quoting "35/100"):
            # keep the explanation, drop the numbers.
            if (o.get("explanation") or "") and _prose_leak(o["explanation"]):
                with get_conn(db_path) as c:
                    c.execute("UPDATE outcomes SET explanation=? WHERE id=?",
                              (sanitize_prose(o["explanation"]), o["id"]))
                fixed += 1
            continue
        try:
            fs = json.loads(o.get("factor_states") or "{}")
        except Exception:
            fs = {}
        if not isinstance(fs, dict) or not fs:
            continue
        enriched = _deterministic_enrich({"band": o.get("band"),
                                          "factor_states": fs}, db_path, names)
        summary = o.get("summary") or ""
        if _NUMERIC_PHRASING_RE.search(summary) and _machine_summary(summary):
            summary = _summarize("", {"factor_state": fs}, [{"id": o.get("id")}], names)
        explanation = o.get("explanation") or ""
        if summary and _prose_leak(summary):
            summary = sanitize_prose(summary)
        if _NUMERIC_PHRASING_RE.search(explanation) and _HEURISTIC_MARKER in explanation:
            explanation = _narrative(
                {"band": o.get("band"), "probability": float(o.get("probability") or 0),
                 "factor_states": fs, "indicators": enriched["indicators"],
                 "risks": enriched["risks"]}, "", names)
        # Whatever route wrote this explanation (deterministic narrative or an
        # older LLM pass), no index numbers survive into the stored row.
        if explanation and _prose_leak(explanation):
            explanation = sanitize_prose(explanation)
        with get_conn(db_path) as c:
            c.execute(
                """UPDATE outcomes SET summary=?, causal_path=?, indicators=?,
                   assumptions=?, risks=?, explanation=? WHERE id=?""",
                (summary, json.dumps(enriched["causal_path"]),
                 json.dumps(enriched["indicators"]), json.dumps(enriched["assumptions"]),
                 json.dumps(enriched["risks"]), explanation, o["id"]))
        fixed += 1
    return fixed


def repair_legacy_outcomes(jid: str, db_path=None) -> None:
    """One-time (per process) rewrite of outcomes created before the
    readability fix: junk names like 'f0 50 / f1 50', 'Representative path
    from N particles' summaries, and raw-JSON strings. Uses the stored
    factor states and factor names — deterministic, no LLM.
    """
    if jid in _repaired:
        return
    _repaired.add(jid)
    try:
        renarrate_outcomes(jid, db_path)
    except Exception:
        pass
    try:
        from app.steps.synthesis import (_deterministic_enrich, _narrative,
                                         _summarize, _direction, _pct)
    except Exception:
        return
    names = factor_names_for_scenario(jid, db_path)
    with get_conn(db_path) as c:
        outs = [dict(r) for r in c.execute(
            "SELECT id,name,probability,band,summary,factor_states,indicators,"
            "assumptions,risks,causal_path,explanation FROM outcomes WHERE scenario_id=?",
            (jid,))]
    for o in outs:
        legacy = (
            bool(_LEGACY_NAME_RE.match(o.get("name") or ""))
            or (o.get("summary") or "").startswith("Representative path from")
            or "Representative path from" in (o.get("explanation") or ""))
        if not legacy:
            continue
        try:
            fs = json.loads(o.get("factor_states") or "{}")
        except Exception:
            fs = {}
        prob = float(o.get("probability") or 0.0)
        band = o.get("band") or "tail"
        rep = {"factor_state": fs}
        new_summary = _summarize("", rep, [{"id": o.get("id")}], names)
        new_name = o.get("name") or "Outcome"
        m = movers(fs, names, limit=1)
        if _LEGACY_NAME_RE.match(new_name):
            if m:
                new_name = f"{m[0][0]} {_direction(m[0][1])}"
            else:
                new_name = f"No major shift · {_pct(prob)} · {band}"
        enriched = _deterministic_enrich(
            {"band": band, "factor_states": fs}, db_path, names)
        explanation = o.get("explanation") or ""
        if "Representative path from" in explanation or not explanation:
            explanation = _narrative(
                {"band": band, "probability": prob, "factor_states": fs,
                 "indicators": enriched["indicators"], "risks": enriched["risks"]},
                "", names)
        with get_conn(db_path) as c:
            c.execute(
                """UPDATE outcomes SET name=?, summary=?, causal_path=?,
                   indicators=?, assumptions=?, risks=?, explanation=? WHERE id=?""",
                (new_name, new_summary, json.dumps(enriched["causal_path"]),
                 json.dumps(enriched["indicators"]), json.dumps(enriched["assumptions"]),
                 json.dumps(enriched["risks"]), explanation, o["id"]))
