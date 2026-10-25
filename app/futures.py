"""Futures-view artifact: aggregate trajectories, branch points, path stories,
and rationale inputs — deterministic from stored data, no LLM required.

Implements openspec/changes/2026-10-25-futures-output (outcome-synthesis
delta): a versioned `futures_view` artifact is written at job completion,
lazily healed for legacy completed jobs, and rebuilt when the job row
changes. Artifact failure never fails a job or a results view.
"""
import json
import re

from app.db import get_conn

ARTIFACT_KIND = "futures_view"
ARTIFACT_VERSION = 2

_JUNK_SUMMARY_RE = re.compile(r"^\d+ particles?$")
_JUNK_TIME_RE = re.compile(r"^step-?\d+$", re.IGNORECASE)
# Synthesized outcome names look like "Outcome 1 · 100% · dominant"; the
# share/band already render separately, so strip that suffix for display.
_SYNTH_NAME_RE = re.compile(r"\s*·\s*\d+(?:\.\d+)?%\s*·\s*\w+\s*$")


def _j(value, limit=8):
    """Parse a stored JSON array (or newline-ish string) into display lines."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(x) for x in value][:limit]
    try:
        v = json.loads(value)
        if isinstance(v, list):
            return [str(x) for x in v][:limit]
    except Exception:
        pass
    import re as _re
    return [s.strip() for s in _re.split(r"[\n;]|, ", str(value)) if s.strip()][:limit]


def possessive(scope: str, name: str) -> str:
    """Scope-aware phrasing: personal jobs address the subject ('your X'),
    macro jobs describe the system in third person ('X')."""
    return f"your {name}" if (scope or "macro") == "personal" else name


# ---------------------------------------------------------------- loading

def _load_job_data(jid: str, db_path=None):
    with get_conn(db_path) as c:
        job = c.execute(
            "SELECT id, premise, scope, horizon, status, outcome_count, updated_at "
            "FROM projection_jobs WHERE id=?", (jid,)).fetchone()
        outcomes = [dict(r) for r in c.execute(
            "SELECT * FROM outcomes WHERE job_id=? ORDER BY probability DESC", (jid,))]
        members: dict[str, list[str]] = {}
        for r in c.execute(
                """SELECT m.outcome_id, m.particle_id FROM outcome_particle_memberships m
                   JOIN outcomes o ON o.id=m.outcome_id WHERE o.job_id=?""", (jid,)):
            members.setdefault(r["outcome_id"], []).append(r["particle_id"])
        particles = {r["id"]: dict(r) for r in c.execute(
            "SELECT id, parent_id, probability, created_step, status "
            "FROM particles WHERE job_id=?", (jid,))}
        states: dict[str, list[dict]] = {}
        for r in c.execute(
                """SELECT ps.particle_id, ps.step_index, ps.factor_values, ps.events
                   FROM particle_states ps JOIN particles p ON p.id=ps.particle_id
                   WHERE p.job_id=? ORDER BY ps.step_index""", (jid,)):
            states.setdefault(r["particle_id"], []).append(dict(r))
        steps = [dict(r) for r in c.execute(
            "SELECT step_index, time_label, summary, turning_points "
            "FROM projection_steps WHERE job_id=? ORDER BY step_index", (jid,))]
        llm_calls = c.execute(
            "SELECT COUNT(*) AS n FROM llm_calls WHERE job_id=?", (jid,)).fetchone()["n"]
    return job, outcomes, members, particles, states, steps, llm_calls


def _baseline(jid: str, db_path=None) -> tuple[dict, float]:
    """Baseline factor values (world_states step 0), defaulting to 50.0."""
    with get_conn(db_path) as c:
        r = c.execute(
            "SELECT factor_values FROM world_states WHERE job_id=? "
            "ORDER BY step_index LIMIT 1", (jid,)).fetchone()
    try:
        fv = json.loads(r["factor_values"]) if r else {}
    except Exception:
        fv = {}
    base = {k: float(v) for k, v in (fv or {}).items() if isinstance(v, (int, float))}
    return base, 50.0


# ---------------------------------------------------------------- aggregates

def _aggregate(members: dict, states: dict) -> dict:
    """Per outcome: per factor {steps, mean, min, max} across member particles."""
    per_outcome: dict[str, dict] = {}
    for oid, pids in members.items():
        vals: dict[str, dict[int, list[float]]] = {}
        for pid in pids:
            for s in states.get(pid, []):
                try:
                    fv = json.loads(s["factor_values"])
                except Exception:
                    continue
                if not isinstance(fv, dict):
                    continue
                try:
                    si = int(s["step_index"])
                except Exception:
                    continue
                for k, v in fv.items():
                    if isinstance(v, (int, float)):
                        vals.setdefault(k, {}).setdefault(si, []).append(float(v))
        series = {}
        for k, by_step in vals.items():
            ss = sorted(by_step)
            series[k] = {
                "steps": ss,
                "mean": [sum(by_step[s]) / len(by_step[s]) for s in ss],
                "min": [min(by_step[s]) for s in ss],
                "max": [max(by_step[s]) for s in ss],
            }
        per_outcome[oid] = series
    return per_outcome


def _end_deltas(series: dict, base_of) -> dict:
    """End-state value minus baseline, per factor, from the mean series."""
    out = {}
    for k, s in series.items():
        if s.get("mean"):
            out[k] = round(s["mean"][-1] - base_of(k), 1)
    return out


# ---------------------------------------------------------------- branch points

def _descendant_outcomes(pid: str, children: dict, particle_outcome: dict) -> set:
    seen, stack, outs = set(), [pid], set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        oid = particle_outcome.get(cur)
        if oid:
            outs.add(oid)
        stack.extend(c for c, _ in children.get(cur, []))
    return outs


def _fork_state(cid: str, cstep: int, states: dict) -> dict | None:
    """Numeric factor state of a particle at (or nearest below) its fork step."""
    rows = states.get(cid, [])
    best = None
    for s in rows:
        try:
            si = int(s["step_index"])
        except Exception:
            continue
        if si == cstep:
            best = s
            break
        if si < cstep:
            best = s
    if best is None:
        return None
    try:
        fv = json.loads(best["factor_values"])
    except Exception:
        return None
    if not isinstance(fv, dict):
        return None
    return {k: float(v) for k, v in fv.items() if isinstance(v, (int, float))}


def _sibling_factor_deltas(groups: list, states: dict, names: dict) -> list[dict]:
    """How sibling branches differ at the fork: per-factor spread across the
    sibling groups' states, largest first. Returns [{id, name, spread}]."""
    group_states = []
    for cid, cstep, _outs in groups:
        fv = _fork_state(cid, cstep, states)
        if fv:
            group_states.append(fv)
    if len(group_states) < 2:
        return []
    keys = set(group_states[0])
    for g in group_states[1:]:
        keys &= set(g)
    deltas = []
    for k in keys:
        vals = [g[k] for g in group_states]
        spread = max(vals) - min(vals)
        if abs(spread) >= 3.0:
            deltas.append({"id": k, "name": names.get(k, k), "spread": round(spread, 1)})
    deltas.sort(key=lambda d: -abs(d["spread"]))
    return deltas[:6]


def detect_branch_points(particles: dict, particle_outcome: dict, states: dict,
                         steps: list[dict], names: dict) -> list[dict]:
    """Branch points = steps where sibling timelines permanently diverged,
    i.e. siblings' descendant sets end in different outcomes. Jobs whose
    particles never diverge yield []."""
    children: dict[str, list[tuple[str, int]]] = {}
    for pid, p in particles.items():
        par = p.get("parent_id")
        if par:
            try:
                children.setdefault(par, []).append((pid, int(p.get("created_step") or 0)))
            except Exception:
                continue
    step_rows = {int(s["step_index"]): s for s in steps}
    points = []
    for _par, kids in children.items():
        if len(kids) < 2:
            continue
        groups = [(cid, cstep, _descendant_outcomes(cid, children, particle_outcome))
                  for cid, cstep in kids]
        nonempty = [g[2] for g in groups if g[2]]
        if len(nonempty) < 2 or len({frozenset(s) for s in nonempty}) < 2:
            continue  # siblings converge on the same outcome: not a fork
        split_step = min(g[1] for g in groups)
        involved = sorted({o for s in nonempty for o in s})
        srow = step_rows.get(split_step, {})
        tp = _j(srow.get("turning_points"))
        events = _j(srow.get("events"))
        points.append({
            "step": split_step,
            "time_label": srow.get("time_label") or f"step-{split_step}",
            "outcomes": involved,
            "factor_deltas": _sibling_factor_deltas(groups, states, names),
            "events": events,
            "turning_points": tp,
            "summary": (srow.get("summary") or "").strip(),
        })
    # One fork per (step, outcome-set); several parents may fork at one step.
    seen: set[tuple] = set()
    unique = []
    for b in sorted(points, key=lambda b: b["step"]):
        key = (b["step"], frozenset(b["outcomes"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(b)
    return unique


# ---------------------------------------------------------------- stories

def _factor_moves(prev: dict, cur: dict, names: dict, thresh=3.0) -> list[dict]:
    moves = []
    for k in sorted(set(prev) | set(cur)):
        a, b = prev.get(k), cur.get(k)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            continue
        d = float(b) - float(a)
        if abs(d) >= thresh:
            moves.append({"name": names.get(k, k), "from": round(float(a), 1),
                          "to": round(float(b), 1), "delta": round(d, 1)})
    moves.sort(key=lambda m: -abs(m["delta"]))
    return moves[:5]


def _milestones(rep_states: list[dict], names: dict, base_of, scope: str) -> list[dict]:
    """Tier-3 story steps derived from the representative trajectory: baseline
    crossings and largest single-step moves. No invented events."""
    pts = []
    prev = None
    for s in rep_states:
        try:
            fv = {k: float(v) for k, v in (json.loads(s["factor_values"]) or {}).items()
                  if isinstance(v, (int, float))}
        except Exception:
            fv = {}
        if not fv:
            prev = None
            continue
        si = int(s["step_index"])
        if prev is not None:
            p_si, p_fv = prev
            # baseline crossings (also when the previous value sits exactly on it)
            for k, v in fv.items():
                b = base_of(k)
                pv = p_fv.get(k)
                if not isinstance(pv, (int, float)):
                    continue
                crossed = ((pv - b) * (v - b) < 0) or (pv == b and abs(v - b) >= 1.0)
                if crossed and abs(v - b) >= 1.0:
                    direction = "rises above" if v > b else "falls below"
                    pts.append({"step": si, "kind": "milestone", "weight": abs(v - b),
                                "text": f"{possessive(scope, names.get(k, k))} {direction} "
                                        f"the baseline at {('step ' + str(si))}",
                                "factor_key": k})
            # largest single-step move
            moves = _factor_moves(p_fv, fv, names, thresh=5.0)
            if moves:
                m = moves[0]
                pts.append({"step": si, "kind": "milestone", "weight": abs(m["delta"]),
                            "text": f"{m['name']} moves {m['delta']:+.0f} points "
                                    f"({m['from']:.0f} → {m['to']:.0f}) at step {si}",
                            "factor_key": m["name"]})
        prev = (si, fv)
    # end state
    last = None
    for s in rep_states:
        try:
            fv = json.loads(s["factor_values"])
        except Exception:
            fv = {}
        if isinstance(fv, dict) and fv:
            last = (int(s["step_index"]), fv)
    if last:
        si, fv = last
        ranked = sorted(((k, v) for k, v in fv.items() if isinstance(v, (int, float))),
                        key=lambda kv: -abs(float(kv[1]) - base_of(kv[1])))
        for k, v in ranked[:3]:
            if abs(v - base_of(k)) >= 3.0:
                pts.append({"step": si, "kind": "milestone", "weight": 1.0,
                            "text": f"By step {si}, {possessive(scope, names.get(k, k))} "
                                    f"ends at {v:.0f}/100",
                            "factor_key": names.get(k, k)})
    # dedupe by (step, text), strongest first per step, keep chronological order
    seen: set[tuple] = set()
    out = []
    for m in sorted(pts, key=lambda m: (m["step"], -m["weight"])):
        key = (m["step"], m["text"])
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


def derive_story(oid: str, rep_pid: str, states: dict, steps: list[dict],
                 outcome: dict, names: dict, base_of, scope: str) -> tuple[list, str]:
    """Story steps with explicit degradation tiers:
    1 events (stored particle events) → 2 step summaries/turning points →
    3 factor milestones. Returns (steps, story_source)."""
    def _row_for(step_index):
        for s in steps:
            if int(s["step_index"]) == step_index:
                return s
        return {}

    def _state_at(pstates, step_index):
        prev = None
        for s in pstates:
            try:
                si = int(s["step_index"])
            except Exception:
                continue
            if si == step_index:
                return s
            if si < step_index:
                prev = s
        return prev or (pstates[0] if pstates else None)

    def _fv_of(row):
        if not row:
            return {}
        try:
            fv = json.loads(row.get("factor_values") or "{}")
        except Exception:
            fv = {}
        return {k: float(v) for k, v in fv.items() if isinstance(v, (int, float))} \
            if isinstance(fv, dict) else {}

    assumptions = _j(outcome.get("assumptions"))
    rep_states = states.get(rep_pid, [])

    # Tier 1: recorded events on the representative particle.
    steps_out = []
    for s in rep_states:
        ev = _j(s.get("events"))
        if not ev:
            continue
        si = int(s["step_index"])
        cur, prev = _fv_of(s), _fv_of(_state_at(rep_states, si - 1))
        steps_out.append({
            "step": si, "label": _row_for(si).get("time_label") or f"step-{si}",
            "text": "; ".join(ev[:3]), "cornerstone": True, "kind": "event",
            "rationale": {"kind": "event", "causal_chain": ev,
                          "factor_moves": _factor_moves(prev, cur, names),
                          "assumptions": assumptions[:3]}})
    if steps_out:
        steps_out.sort(key=lambda x: x["step"])
        return steps_out[:8], "events"

    # Tier 2: shared step summaries + turning points (skip junk summaries).
    for s in steps:
        si = int(s["step_index"])
        summary = (s.get("summary") or "").strip()
        tp = _j(s.get("turning_points"))
        if _JUNK_SUMMARY_RE.match(summary):
            summary = ""
        if not summary and not tp:
            continue
        cur, prev = _fv_of(_state_at(rep_states, si)), _fv_of(_state_at(rep_states, si - 1))
        text = tp[0] if tp else summary
        steps_out.append({
            "step": si, "label": s.get("time_label") or f"step-{si}",
            "text": text[:220], "cornerstone": bool(tp), "kind": "step",
            "rationale": {"kind": "turning-point" if tp else "step",
                          "causal_chain": tp or ([summary] if summary else []),
                          "factor_moves": _factor_moves(prev, cur, names),
                          "assumptions": assumptions[:3]}})
    if steps_out:
        steps_out.sort(key=lambda x: x["step"])
        return steps_out[:8], "steps"

    # Tier 3: factor milestones from the representative trajectory.
    for m in _milestones(rep_states, names, base_of, scope)[:8]:
        si = m["step"]
        cur, prev = _fv_of(_state_at(rep_states, si)), _fv_of(_state_at(rep_states, si - 1))
        steps_out.append({
            "step": si, "label": _row_for(si).get("time_label") or f"step-{si}",
            "text": m["text"], "cornerstone": True, "kind": "milestone",
            "rationale": {"kind": "milestone",
                          "causal_chain": ["Derived from the stored factor trajectory "
                                           "(no recorded events for this run)"],
                          "factor_moves": _factor_moves(prev, cur, names),
                          "assumptions": assumptions[:3]}})
    return steps_out, "milestones"


# ---------------------------------------------------------------- build/save

def build_futures_view(jid: str, db_path=None) -> dict | None:
    from app.present import factor_names_for_job
    job, outcomes, members, particles, states, steps, llm_calls = _load_job_data(jid, db_path)
    if not job:
        return None
    names = factor_names_for_job(jid, db_path)
    baseline, base_default = _baseline(jid, db_path)

    def base_of(k):
        return baseline.get(k, base_default)

    agg = _aggregate(members, states)
    particle_outcome: dict[str, str] = {}
    for oid, pids in members.items():
        for pid in pids:
            particle_outcome.setdefault(pid, oid)
    branch_points = detect_branch_points(particles, particle_outcome, states, steps, names)
    scope = job["scope"] if "scope" in job.keys() else "macro"

    out_rows = []
    sources = []
    for o in outcomes:
        oid = o["id"]
        series = agg.get(oid, {})
        story, source = derive_story(oid, o.get("representative_particle") or "",
                                     states, steps, o, names, base_of, scope)
        sources.append(source)
        out_rows.append({
            "id": oid,
            "name": o["name"] or "Outcome",
            "short_name": (_SYNTH_NAME_RE.sub("", o["name"] or "Outcome").strip()
                           or o["name"] or "Outcome"),
            "band": o["band"] or "tail",
            "probability": float(o["probability"] or 0.0),
            "share_pct": round(float(o["probability"] or 0.0) * 100),
            "members": len(members.get(oid, [])),
            "representative": o.get("representative_particle") or "",
            "summary": o["summary"] or "",
            "explanation": o["explanation"] or "",
            "indicators": _j(o.get("indicators")),
            "assumptions": _j(o.get("assumptions")),
            "risks": _j(o.get("risks")),
            "causal_path": _j(o.get("causal_path")),
            "end_deltas": [{"id": k, "name": names.get(k, k), "delta": v}
                           for k, v in sorted(_end_deltas(series, base_of).items(),
                                              key=lambda kv: -abs(kv[1]))],
            "series": series,
            "story": story,
            "story_source": source,
        })

    factor_keys = sorted({k for r in out_rows for k in r["series"]})
    # Human time labels only when they are not junk like "step-3".
    tl = {int(s["step_index"]): (s.get("time_label") or "") for s in steps}
    human_time = any(v and not _JUNK_TIME_RE.match(v) for v in tl.values())

    dominant = max(sources, key=lambda s: (s == "events", s == "steps")) if sources else "milestones"
    return {
        "version": ARTIFACT_VERSION,
        "job_id": jid,
        "premise": job["premise"],
        "scope": scope,
        "horizon": job["horizon"] if "horizon" in job.keys() else "auto",
        "job_updated_at": job["updated_at"],
        "heuristic_mode": llm_calls == 0,
        "timelines_explored": len(particles),
        "story_source": dominant,
        "factors": [{"id": k, "name": names.get(k, k)} for k in factor_keys],
        "baseline": baseline,
        "baseline_default": base_default,
        "human_time_labels": human_time,
        "time_labels": tl,
        "outcomes": out_rows,
        "branch_points": branch_points,
    }


def save_futures_view(jid: str, payload: dict, db_path=None) -> dict:
    from app.store import write_artifact
    return write_artifact(jid, ARTIFACT_KIND, payload, db_path)


def load_futures_view(jid: str, db_path=None, heal: bool = True) -> dict | None:
    """Load the newest futures_view artifact; lazily build+persist for
    completed jobs that predate it; rebuild when the job row changed or the
    artifact version differs. Never raises."""
    with get_conn(db_path) as c:
        job = c.execute("SELECT status, updated_at FROM projection_jobs WHERE id=?",
                        (jid,)).fetchone()
        if not job:
            return None
        r = c.execute("""SELECT path FROM artifacts WHERE job_id=? AND kind=?
                         ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                      (jid, ARTIFACT_KIND)).fetchone()
    payload = None
    if r:
        try:
            from app.store import read_artifact
            payload = read_artifact(r["path"])
        except Exception:
            payload = None
    stale = payload is not None and (
        payload.get("job_updated_at") != job["updated_at"]
        or payload.get("version") != ARTIFACT_VERSION)
    if payload is None or stale:
        if not heal or job["status"] != "COMPLETED":
            return payload
        try:
            built = build_futures_view(jid, db_path)
            if built:
                save_futures_view(jid, built, db_path)
                return built
        except Exception:
            return payload
    return payload
