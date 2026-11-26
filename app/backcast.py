"""Backcast: from each projected outcome, work backwards to the decisions that
would make that future materialise.

The rest of the pipeline is descriptive. It says what happens, how likely, what
to watch, and where paths fork — but never what to do. This is the action layer.

The design constraint that makes it honest: the decisions are *grounded in the
simulation*, not invented. Two stored signals decide what matters:

- fork spreads (`futures.detect_branch_points`) say which factors actually
  separated the timelines. A factor with a wide spread at the earliest fork is
  the pivot a decision can act on; a factor that never diverged is not.
- causal edges (`causal_graphs`) say which factors drive those pivots, so a
  recommendation can target an upstream lever rather than a symptom.

The LLM's job is therefore prioritisation and phrasing, not discovery. It is
told the pivots, the drivers, and the decision context, and asked to name
concrete near-term moves that would push those pivots toward a given outcome.

Everything here is labelled as interpretation (it is a model's reading of a
simulated future, not a derived fact), and a deterministic fallback produces
the same pivots as imperative statements when no provider is available, so the
feature degrades instead of vanishing.
"""
from __future__ import annotations

import json
import logging
import re

from app.db import get_conn
from app.scenarios.repos import get_scenario

logger = logging.getLogger(__name__)

# How many pivots and drivers to show. Beyond this the advice stops being
# prioritised and starts being a list.
MAX_PIVOTS = 4
MAX_DRIVERS = 3
MAX_DECISIONS = 4

BACKCAST_PROMPT = """You are a strategic backcasting analyst.

You are given one projected future of a scenario, the premise, the user's
decision context, and the factors that actually separated this future from its
alternatives in the simulation (the "pivots", with how far each diverged) plus
the factors that causally drive each pivot.

Name the near-term decisions that would make this future more likely.

Rules:
- Ground every decision in the pivots and drivers you are given. A decision
  must move (or protect) one of the named factors. Do not invent factors.
- A decision is a concrete action a person or organisation could take in the
  next 12-24 months, not a category ("invest in resilience" is too vague;
  "commit to a fixed weekly review of income vs. burn" is a decision).
- Prefer acting on drivers over symptoms: if a pivot is driven by an upstream
  factor, the decision should target the upstream factor.
- Do not give generic life or business advice. If the pivots do not support a
  decision, return fewer decisions rather than filling the list.
- Never quote internal index numbers, never write 0-100 values, never say
  "baseline". Refer to a factor by name and direction in words.
- If a decision context is given, the decisions must be relevant to that
  decision.

Respond ONLY with JSON:
{"decisions": [{"decision": str, "targets": str, "why": str}],
 "caveat": str}

- decision: the action, one sentence, imperative.
- targets: the factor name it acts on, copied from the pivots/drivers.
- why: one sentence on how this pushes the outcome, referencing the simulation.
- caveat: one sentence on what this advice cannot tell the reader.
"""


# ---------------------------------------------------------------- simulation signals

def _causal_edges(jid: str, db_path=None) -> list[dict]:
    with get_conn(db_path) as c:
        r = c.execute(
            "SELECT edges FROM causal_graphs WHERE scenario_id=? ORDER BY id DESC LIMIT 1",
            (jid,)).fetchone()
    if not r:
        return []
    try:
        edges = json.loads(r["edges"] or "[]")
    except Exception:
        return []
    return [e for e in edges if isinstance(e, dict)
            and e.get("source") and e.get("target")]


def _drivers(factor_id: str, edges: list[dict], names: dict) -> list[str]:
    """Upstream factors that push `factor_id`, strongest first.

    A causal edge is source -> target, so the drivers of a pivot are the
    sources of edges pointing at it. Strength is signed (a negative strength is
    an inverse relationship), so magnitude decides ordering.
    """
    ups = [e for e in edges if e.get("target") == factor_id]
    ups.sort(key=lambda e: -abs(float(e.get("strength") or 0)))
    out = []
    for e in ups[:MAX_DRIVERS]:
        n = names.get(e["source"], e["source"])
        if n not in out:
            out.append(n)
    return out


def _member_spread(jid: str, oid: str, db_path=None) -> dict[str, float]:
    """Per-factor range across this outcome's timelines, at each timeline's end.

    A factor that ends at the same level in every timeline leading to this
    future is not something a decision can move — the simulation already has it
    pinned. A wide range is where the timelines genuinely parted, so that is
    where leverage lives.
    """
    with get_conn(db_path) as c:
        pids = [r["particle_id"] for r in c.execute(
            "SELECT particle_id FROM outcome_particle_memberships WHERE outcome_id=?",
            (oid,))]
        if not pids:
            return {}
        q = ",".join("?" * len(pids))
        rows = c.execute(
            f"SELECT particle_id, step_index, factor_values FROM particle_states "
            f"WHERE particle_id IN ({q})", pids).fetchall()
    # Final state per timeline.
    last: dict[str, tuple[int, str]] = {}
    for r in rows:
        try:
            si = int(r["step_index"])
        except Exception:
            continue
        cur = last.get(r["particle_id"])
        if cur is None or si > cur[0]:
            last[r["particle_id"]] = (si, r["factor_values"])
    vals: dict[str, list[float]] = {}
    for _pid, (_si, raw) in last.items():
        try:
            fv = json.loads(raw or "{}")
        except Exception:
            continue
        for k, v in (fv or {}).items():
            if isinstance(v, (int, float)):
                vals.setdefault(k, []).append(float(v))
    out = {}
    for k, vs in vals.items():
        if len(vs) >= 2:
            out[k] = max(vs) - min(vs)
    return out


def _outcome_pivots(outcome_id: str, jid: str, db_path=None) -> list[dict]:
    """The factors this future actually hinges on, from stored simulation data.

    Two grounded signals, no forked-particle tree required (the engine may
    never have split a timeline — in that case `detect_branch_points` returns
    nothing and a fork-based backcast would have nothing to say at all):

    - how far the factor ended from where it started in this outcome, which is
      what makes this future *this* future rather than another;
    - how widely it ranged across the timelines that landed here, which is what
      a decision could still have moved.

    Ranked by the second within the first: the defining factors that are also
    undetermined are the ones worth acting on.
    """
    from app.futures import build_futures_view
    fv = build_futures_view(jid, db_path)
    if not fv:
        return []
    names = {f["id"]: f["name"] for f in fv.get("factors") or []}
    edges = _causal_edges(jid, db_path)
    row = next((o for o in fv.get("outcomes") or [] if o.get("id") == outcome_id), None)
    if row is None:
        return []
    spread = _member_spread(jid, outcome_id, db_path)
    pivots = []
    for d in row.get("end_deltas") or []:
        fid = d.get("id")
        if not fid:
            continue
        moved = abs(float(d.get("delta") or 0))
        varies = float(spread.get(fid) or 0)
        if moved < 8.0:      # barely left its starting level: not what defines it
            continue
        pivots.append({
            "id": fid,
            "name": d.get("name") or names.get(fid, fid),
            "moved": round(moved, 1),
            "varies": round(varies, 1),
            "direction": d.get("phrase") or "",
            "drivers": _drivers(fid, edges, names),
            "time_label": "",
            # Actionable = defined AND undetermined. A factor pinned at the same
            # level in every timeline scores zero: nothing a reader does moves it.
            "score": moved * (varies if varies >= 5.0 else 0.0),
        })
    pivots.sort(key=lambda p: -p["score"])
    return pivots[:MAX_PIVOTS]


# ---------------------------------------------------------------- deterministic fallback

def heuristic_decisions(pivots: list[dict], outcome_name: str,
                        decision_context: str = "") -> dict:
    """No-LLM backcast: the simulation's own levers, stated as levers.

    Deliberately does not invent a specific action. The stored data can say
    which factors this future hinges on and that they were genuinely open; it
    cannot know the reader's options. Claiming otherwise would be the generic
    advice this feature exists to avoid.
    """
    if not pivots:
        return {"decisions": [], "caveat": ""}
    decisions = []
    for p in pivots[:MAX_DECISIONS]:
        target = p["name"]
        driver = (p.get("drivers") or [None])[0]
        why = (f"{target} ends {p['direction']} in this future and varied by "
               f"{p['varies']:.0f} points across the timelines that reach it, so "
               f"it stayed open — a decision can still move it.")
        if driver:
            decision = (f"Work on {driver}, upstream of {target}, rather than on "
                        f"{target} itself")
        else:
            decision = (f"Decide what pulls {target} back toward where it started; "
                        f"nothing upstream of it is recorded")
        decisions.append({"decision": decision, "targets": target, "why": why})
    return {
        "decisions": decisions,
        "caveat": ("Derived from the stored factor trajectories without a language "
                   "model: these identify the levers, not the specific moves."),
    }


# ---------------------------------------------------------------- LLM phase

def _payload(jid: str, outcome: dict, pivots: list[dict],
             decision_context: str) -> str:
    parts = []
    scenario = get_scenario(jid) or {}
    premise = (scenario.get("premise") or "")[:600]
    parts.append(f"Premise: {premise}")
    if decision_context:
        parts.append(f"Decision context: {decision_context[:600]}")
    parts.append(f"Future: {outcome.get('short_name') or outcome.get('name')} "
                 f"(band: {outcome.get('band') or 'n/a'})")
    if outcome.get("summary"):
        parts.append(f"What this future is: {outcome['summary'][:700]}")
    if pivots:
        lines = []
        for p in pivots:
            drv = f"; driven by {', '.join(p['drivers'])}" if p.get("drivers") else ""
            lines.append(f"- {p['name']}: ends {p['direction']} in this future, "
                         f"and varied by {p['varies']:.0f} points across the "
                         f"timelines that reach it{drv}")
        parts.append("Factors this future hinges on (moved a lot, and were still "
                     "open across the timelines that reach it):\n"
                     + "\n".join(lines))
    else:
        parts.append("Hinges on: nothing identified — every factor ended close to "
                     "where it started, so the simulation gives no evidence for "
                     "what would change this future. Return at most one decision "
                     "and say so in the caveat.")
    return "\n\n".join(parts)


def llm_decisions(jid: str, outcome: dict, pivots: list[dict],
                  decision_context: str = "", db_path=None,
                  client=None) -> dict | None:
    """Ask the model to turn the pivots into named decisions. None on failure.

    `client` is injectable so the response handling (filtering decisions that
    do not map to a real factor, dropping leaked index numbers) can be tested
    without a provider.
    """
    try:
        from app.llm.client import LLMClient, model_for_phase
        # This runs inside an interactive request, so it must fail fast: the
        # default client retries 4x with a 120s read timeout, which would hold
        # the request open for minutes and then 502. One attempt, short timeout
        # — the deterministic reading is already on screen if this returns None.
        if client is None:
            client = LLMClient(db_path=db_path, timeout_s=20, max_attempts=1)
        out = client.chat(jid, "backcast", [
            {"role": "system", "content": BACKCAST_PROMPT},
            {"role": "user", "content": _payload(jid, outcome, pivots, decision_context)},
        ], model=model_for_phase("backcast"))["json"]
    except Exception as e:
        logger.warning("backcast LLM call failed (%s); using simulation pivots", e)
        return None
    if not isinstance(out, dict):
        return None
    raw = out.get("decisions")
    if not isinstance(raw, list):
        return None
    allowed = {p["name"] for p in pivots}
    decisions = []
    for d in raw[:MAX_DECISIONS]:
        if not isinstance(d, dict):
            continue
        text = _clean(d.get("decision"))
        if not text:
            continue
        target = _clean(d.get("targets"))
        # A decision must act on a factor that came from the simulation.
        if allowed and target and target not in allowed:
            target = _match_factor(target, allowed)
        decisions.append({
            "decision": text,
            "targets": target or "",
            "why": _clean(d.get("why")),
        })
    if not decisions:
        return None
    caveat = _clean(out.get("caveat")) or (
        "A model's reading of the simulated pivots, not a recommendation "
        "grounded in your full circumstances.")
    return {"decisions": decisions, "caveat": caveat}


def _match_factor(target: str, allowed: set[str]) -> str:
    """Map a loosely-named target onto a real factor name, or ''."""
    low = target.lower()
    for a in allowed:
        if a.lower() == low:
            return a
    for a in allowed:
        if low in a.lower() or a.lower() in low:
            return a
    return ""


_INDEX_RE = re.compile(r"/100|out of 100|\bbaselines?\b|100-point", re.I)


def _clean(value) -> str:
    """Trim, collapse whitespace, and keep model output out of the index register."""
    if not isinstance(value, str):
        return ""
    from app.narrate import sanitize_prose
    text = re.sub(r"\s+", " ", sanitize_prose(value)).strip()
    if _INDEX_RE.search(text):
        return ""  # unusable: it leaked storage vocabulary after sanitising
    return text[:400]


# ---------------------------------------------------------------- entry point

def build_backcast(jid: str, db_path=None) -> dict:
    """Backcast every outcome of a completed scenario from the simulation alone.

    Deterministic and fast: no provider call, so the panel always renders. The
    model reading is layered on per outcome by `enhance_outcome`, which each
    card fetches separately so one slow or failing call cannot block the page.
    """
    with get_conn(db_path) as c:
        outs = [dict(r) for r in c.execute(
            "SELECT id, name, band, summary FROM outcomes WHERE scenario_id=? "
            "ORDER BY probability DESC", (jid,))]
    scenario = get_scenario(jid) or {}
    decision_context = ""
    try:
        decision_context = scenario.get("decision_context") or ""
    except Exception:
        pass

    results: dict[str, dict] = {}
    for o in outs:
        pivots = _outcome_pivots(o["id"], jid, db_path)
        fb = heuristic_decisions(pivots, o.get("short_name") or o.get("name") or "",
                                 decision_context)
        results[o["id"]] = {**fb, "pivots": pivots, "source": "simulation"}
    return {"outcomes": results, "source": "simulation"}


def enhance_outcome(jid: str, oid: str, db_path=None) -> dict | None:
    """One outcome's decisions from the model, or None if it is unavailable.

    Kept separate from build_backcast so the interactive page never waits on a
    provider: the deterministic reading is already visible and this replaces it
    only if it succeeds.
    """
    # Cheap gate before an interactive call: when the provider is already
    # known-bad, do not spend 20s per outcome discovering it again. The
    # deterministic reading is already on the page.
    try:
        from app.llm.client import provider_open
        if not provider_open():
            return None
    except Exception:
        pass
    with get_conn(db_path) as c:
        r = c.execute("SELECT id, name, band, summary FROM outcomes "
                      "WHERE id=? AND scenario_id=?", (oid, jid)).fetchone()
    if not r:
        return None
    o = dict(r)
    pivots = _outcome_pivots(oid, jid, db_path)
    scenario = get_scenario(jid) or {}
    try:
        decision_context = scenario.get("decision_context") or ""
    except Exception:
        decision_context = ""
    payload = llm_decisions(jid, o, pivots, decision_context, db_path)
    if not payload:
        return None
    return {**payload, "pivots": pivots, "source": "llm"}


def set_outcome_backcast(jid: str, oid: str, entry: dict, db_path=None) -> None:
    """Store one outcome's entry, keeping the others intact."""
    stored = load_backcast(jid, db_path)
    stored[oid] = entry
    source = "llm" if any((v or {}).get("source") == "llm" for v in stored.values()) \
        else "simulation"
    save_backcast(jid, {"outcomes": stored, "source": source}, db_path)


def save_backcast(jid: str, result: dict, db_path=None) -> None:
    with get_conn(db_path) as c:
        c.execute("UPDATE projection_scenarios SET backcast=? WHERE id=?",
                  (json.dumps(result), jid))


def load_backcast(jid: str, db_path=None) -> dict:
    """Stored backcast as {outcome_id: entry}; {} when none has been run."""
    with get_conn(db_path) as c:
        r = c.execute("SELECT backcast FROM projection_scenarios WHERE id=?",
                      (jid,)).fetchone()
    if not r or not r["backcast"]:
        return {}
    try:
        data = json.loads(r["backcast"])
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    outs = data.get("outcomes")
    return outs if isinstance(outs, dict) else {}
