"""Projection engine: proposer, validation, adaptive steps, branch/merge/prune, stopping, checkpoint."""
import json, math, uuid
from app.db import get_conn

def validate_development(dev: dict, reality_mode="grounded") -> str | None:
    p = dev.get("probability", -1)
    if not (0.0 <= p <= 1.0): return "probability out of bounds"
    if dev.get("kind", "drift") not in ("drift", "branch", "tail", "event"): return "bad kind"
    if reality_mode == "grounded" and dev.get("violates_physics"): return "grounded constraint violated"
    return None

def adaptive_step(volatility: float, llm_rec: int | None = None, db_path=None) -> int:
    from app.config import settings
    base = 90 if volatility < 0.4 else (30 if volatility < 0.7 else 14)
    step = llm_rec or base
    return max(settings.step_min_days, min(settings.step_max_days, step))

def should_branch(dev: dict, db_path=None) -> str | None:
    from app.config import settings
    p, imp = dev.get("probability", 0), dev.get("impact", 0)
    if p >= settings.branch_prob_threshold and imp >= 0.3: return "branch"
    if p < settings.branch_prob_threshold and imp >= settings.tail_risk_impact_threshold:
        return "tail"
    if p >= 0.5: return "drift"
    return None  # trivial low-p low-impact discarded

def factor_distance(a: dict, b: dict) -> float:
    keys = set(a) | set(b)
    if not keys: return 0.0
    return math.sqrt(sum(((a.get(k, 50) - b.get(k, 50)) / 100.0) ** 2 for k in keys)
                     * (100.0 ** 2) / max(1, len(keys)))

def merge_prune(particles: list[dict], db_path=None) -> list[dict]:
    from app.config import settings
    # merge by normalized distance
    merged: list[dict] = []
    for p in sorted(particles, key=lambda x: -x.get("probability", 0)):
        placed = False
        for m in merged:
            if factor_distance(p.get("factor_state", {}), m.get("factor_state", {})) < settings.merge_distance_threshold:
                m["probability"] += p.get("probability", 0)
                placed = True; break
        if not placed: merged.append(dict(p))
    # prune to beam width: keep top (width-reserved) + most divergent for reserved slots
    w, r = settings.beam_width, settings.beam_reserved_divergent
    if len(merged) <= w: return [p for p in merged if p.get("probability", 0) > 0.005]
    top = sorted(merged, key=lambda x: -x["probability"])[: w - r]
    rest = sorted(merged, key=lambda x: -x["probability"])[w - r:]
    div = sorted(rest, key=lambda x: x["probability"])[:r]  # divergent low-p kept
    # prune stagnant/inconsistent
    out = [p for p in top + div if p.get("probability", 0) > 0.005 and not p.get("inconsistent")]
    # renormalize later at synthesis; keep mass here
    return out[:w]

def stopping_reason(step: int, budget: dict, clusters_stable: bool = False,
                    novelty_stalled: int = 0, horizon_reached: bool = False) -> str | None:
    from app.scenarios.repos import check_budget
    ok, why = check_budget(budget)
    if not ok: return f"budget:{why}"
    if horizon_reached: return "horizon"
    if clusters_stable: return "convergence"
    if novelty_stalled >= 3: return "diminished_novelty"
    return None

def run_step(scenario_id: str, step: int, particles: list[dict], developments_fn=None,
             reality_mode="grounded", budget: dict | None = None, db_path=None,
             llm_mode: bool = False, step_events: dict | None = None,
             causal_edges: list[dict] | None = None) -> dict:
    """One projection step across particles with checkpoint write.

    llm_mode marks that developments came from the LLM proposer (vs the
    heuristic fallback). step_events maps particle id -> {summary, events}
    returned by the proposer; they are persisted on particle_states and folded
    into the shared step summary so stories can cite real recorded events.

    causal_edges is the scenario's causal graph. When supplied, a development's
    direct deltas are propagated along it (app.steps.causality), so factors move
    because a driver moved rather than because a hash picked them. The hop each
    factor took is recorded per particle, so an outcome's order of effect is
    measured instead of assumed.
    """
    from app.scenarios.repos import idem_key, idem_get, idem_put, save_checkpoint
    budget = budget or {}
    new_particles: list[dict] = []
    # particle id -> {factor: hop}; 0 = named by the development, >=1 = induced
    # downstream along the causal graph.
    particle_hops: dict[str, dict[str, int]] = {}
    # particle id -> {hop: total absolute movement}, accumulated over the run.
    particle_mass: dict[str, dict[str, float]] = {}
    for p in particles:
        key = idem_key(scenario_id, "step", step, p.get("id", ""), str(sorted(p.get("factor_state", {}).keys())))
        cached = idem_get(key, db_path)
        if cached:
            new_particles.extend(cached["particles"]); continue
        devs = (developments_fn(p) if developments_fn else [{"kind": "drift",
                "probability": 0.6, "impact": 0.4, "delta": {}}])
        made = []
        for d in devs:
            if validate_development(d, reality_mode): continue
            mode = should_branch(d)
            if mode is None: continue
            delta = d.get("delta", {})
            hops, mass = {}, {}
            if causal_edges:
                from app.steps.causality import apply_cascade, hop_mass, split_seed
                seed = split_seed(set(p.get("factor_state", {})), delta)
                fs, hops, induced = apply_cascade(
                    p.get("factor_state", {}), seed, causal_edges)
                mass = hop_mass(seed, induced)
            else:
                # No graph: the development is the whole effect.
                fs = dict(p.get("factor_state", {}))
                for k, v in delta.items():
                    fs[k] = max(0.0, min(100.0, fs.get(k, 50) + v))
            if mode == "drift":
                pid = p.get("id", uuid.uuid4().hex[:8])
                made.append({"id": pid, "factor_state": fs,
                             "probability": p.get("probability", 1.0)})
            else:
                pid = uuid.uuid4().hex[:8]
                made.append({"id": pid, "parent": p.get("id"),
                             "factor_state": fs,
                             "probability": p.get("probability", 1.0) * d.get("probability", 0.5)})
            # Attribute the hops to the particle that actually carries this state.
            # Keying them to the parent dropped the record for every branch
            # child (which gets a fresh id), so those timelines reported no
            # order at all. Merge rather than overwrite: a particle can receive
            # several developments in one step and the shallowest attribution of
            # each factor is the one worth keeping.
            if hops:
                acc_hops = particle_hops.setdefault(pid, {})
                for k, v in hops.items():
                    acc_hops[k] = min(v, acc_hops.get(k, v))
            if mass:
                acc_mass = particle_mass.setdefault(pid, {})
                for k, v in mass.items():
                    acc_mass[k] = round(acc_mass.get(k, 0.0) + v, 3)
        if not made:
            made = [p]
        idem_put(key, {"particles": made}, db_path)
        new_particles.extend(made)
    pruned = merge_prune(new_particles)
    budget["steps"] = budget.get("steps", 0) + 1
    step_events = step_events or {}
    rec_summaries = [v.get("summary") for v in step_events.values() if v.get("summary")]
    rec_events = [e for v in step_events.values() for e in v.get("events", []) if e]

    def _write_step_rows():
        with get_conn(db_path) as c:
            # stub parents first so parent_id FK always resolves
            for pt in pruned:
                par = pt.get("parent")
                if par:
                    c.execute("""INSERT OR IGNORE INTO particles(id,scenario_id,probability,factor_state,created_step)
                      VALUES(?,?,0.0,'{}',?)""", (par, scenario_id, step))
            for pt in pruned:
                ev = step_events.get(pt.get("id", "")) or step_events.get(pt.get("parent", "")) or {}
                c.execute("""INSERT OR REPLACE INTO particles(id,scenario_id,parent_id,probability,
                  factor_state,created_step) VALUES(?,?,?,?,?,?)""",
                  (pt.get("id", uuid.uuid4().hex[:8]), scenario_id, pt.get("parent"),
                   pt.get("probability", 0), json.dumps(pt.get("factor_state", {})), step))
                c.execute("""INSERT OR REPLACE INTO particle_states(particle_id,step_index,
                  factor_values,events,causal_hops,causal_source) VALUES(?,?,?,?,?,?)""",
                  (pt.get("id"), step, json.dumps(pt.get("factor_state", {})),
                   json.dumps(ev.get("events", [])),
                   json.dumps({"hops": particle_hops.get(pt.get("id", ""), {}),
                               "mass": particle_mass.get(pt.get("id", ""), {})}),
                   "graph" if causal_edges else "none"))
            # Shared step narrative: recorded events/summaries when the LLM
            # proposer ran; a neutral step marker otherwise (junk-filtered later).
            if rec_summaries or rec_events:
                summary = "; ".join((rec_summaries + rec_events)[:4])[:400]
                c.execute("""INSERT OR REPLACE INTO projection_steps(scenario_id,step_index,time_label,summary,turning_points)
                  VALUES(?,?,?,?,?)""", (scenario_id, step, f"step-{step}", summary,
                                          json.dumps(rec_events[:3])))
            else:
                c.execute("""INSERT OR REPLACE INTO projection_steps(scenario_id,step_index,time_label,summary)
                  VALUES(?,?,?,?)""", (scenario_id, step, f"step-{step}", f"{len(pruned)} particles"))
    from app.scenarios.repos import _write_retry
    _write_retry(_write_step_rows)
    save_checkpoint(scenario_id, "projection_loop", step,
                    {"particles": pruned, "events": rec_events}, budget, db_path)
    out = {"particles": pruned, "budget": budget, "llm_mode": llm_mode}
    if rec_events:
        out["events"] = rec_events
    return out
