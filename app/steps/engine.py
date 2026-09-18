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
    from app.jobs.repositories import check_budget
    ok, why = check_budget(budget)
    if not ok: return f"budget:{why}"
    if horizon_reached: return "horizon"
    if clusters_stable: return "convergence"
    if novelty_stalled >= 3: return "diminished_novelty"
    return None

def run_step(job_id: str, step: int, particles: list[dict], developments_fn=None,
             reality_mode="grounded", budget: dict | None = None, db_path=None) -> dict:
    """One deterministic projection step across particles with checkpoint write."""
    from app.jobs.repositories import idem_key, idem_get, idem_put, save_checkpoint
    budget = budget or {}
    new_particles: list[dict] = []
    for p in particles:
        key = idem_key(job_id, "step", step, p.get("id", ""), str(sorted(p.get("factor_state", {}).keys())))
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
            fs = dict(p.get("factor_state", {}))
            for k, v in delta.items():
                fs[k] = max(0.0, min(100.0, fs.get(k, 50) + v))
            if mode == "drift":
                made.append({"id": p.get("id", uuid.uuid4().hex[:8]),
                             "factor_state": fs, "probability": p.get("probability", 1.0)})
            else:
                made.append({"id": uuid.uuid4().hex[:8], "parent": p.get("id"),
                             "factor_state": fs,
                             "probability": p.get("probability", 1.0) * d.get("probability", 0.5)})
        if not made:
            made = [p]
        idem_put(key, {"particles": made}, db_path)
        new_particles.extend(made)
    pruned = merge_prune(new_particles)
    budget["steps"] = budget.get("steps", 0) + 1
    with get_conn(db_path) as c:
        # stub parents first so parent_id FK always resolves
        for pt in pruned:
            par = pt.get("parent")
            if par:
                c.execute("""INSERT OR IGNORE INTO particles(id,job_id,probability,factor_state,created_step)
                  VALUES(?,?,0.0,'{}',?)""", (par, job_id, step))
        for pt in pruned:
            c.execute("""INSERT OR REPLACE INTO particles(id,job_id,parent_id,probability,
              factor_state,created_step) VALUES(?,?,?,?,?,?)""",
              (pt.get("id", uuid.uuid4().hex[:8]), job_id, pt.get("parent"),
               pt.get("probability", 0), json.dumps(pt.get("factor_state", {})), step))
            c.execute("""INSERT OR REPLACE INTO particle_states(particle_id,step_index,factor_values,events)
              VALUES(?,?,?,?)""", (pt.get("id"), step, json.dumps(pt.get("factor_state", {})), "[]"))
        c.execute("""INSERT OR REPLACE INTO projection_steps(job_id,step_index,time_label,summary)
          VALUES(?,?,?,?)""", (job_id, step, f"step-{step}", f"{len(pruned)} particles"))
    save_checkpoint(job_id, "projection_loop", step,
                    {"particles": pruned}, budget, db_path)
    return {"particles": pruned, "budget": budget}
