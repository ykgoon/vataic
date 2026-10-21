"""Presentation helpers shared by job-detail and results views: human
probability strings, factor-name mapping, parsed JSON lists, outcome rows.

Also heals legacy outcome rows (pre-readability pipeline) lazily on first
view, so completed jobs run before the fix still render sensibly.
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


def factor_names_for_job(jid: str, db_path=None) -> dict:
    """Map factor id -> human name for a job's selected factor set."""
    with get_conn(db_path) as c:
        r = c.execute(
            "SELECT factors FROM factor_sets WHERE job_id=? ORDER BY id DESC LIMIT 1",
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
    names = factor_names_for_job(jid, db_path)
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
        from app.steps.synthesis import (_deterministic_enrich, _narrative,
                                         _summarize, _direction, _pct)
    except Exception:
        return
    names = factor_names_for_job(jid, db_path)
    with get_conn(db_path) as c:
        outs = [dict(r) for r in c.execute(
            "SELECT id,name,probability,band,summary,factor_states,indicators,"
            "assumptions,risks,causal_path,explanation FROM outcomes WHERE job_id=?",
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
                new_name = f"{m[0][0]} ends {_direction(m[0][1])}"
            else:
                new_name = f"Near-baseline scenario · {_pct(prob)} · {band}"
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
