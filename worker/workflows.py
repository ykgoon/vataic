"""Projection pipeline: plain in-process activities + run_sync.

No Temporal. The DB state machine, checkpoints, idempotency records, and
leases (in app/jobs/repositories.py) provide all durable-execution
semantics. run_sync() runs the full pipeline as a plain Python loop.
"""
import json

# ---- activities (plain functions, idempotent) ----
def _put(key: str, res: dict) -> dict:
    from app.jobs.repositories import idem_put
    idem_put(key, res)
    return res


def act_parse(inp: dict) -> dict:
    from app.steps.intake import parse_scenario
    from app.jobs.repositories import idem_key, idem_get
    key = idem_key(inp["job_id"], "parse_scenario", 0, "", inp.get("premise", "")[:32])
    hit = idem_get(key)
    return hit if hit else _put(key, parse_scenario(
        inp["job_id"], inp.get("premise", ""), inp.get("scope", "macro"),
        inp.get("reality_mode", "grounded"), inp.get("horizon", "auto")))


def act_factors(inp: dict) -> dict:
    from app.steps.factors import default_candidates, select_factors
    from app.jobs.repositories import idem_key, idem_get
    key = idem_key(inp["job_id"], "select_factors", 0, "", inp.get("scope", "macro"))
    hit = idem_get(key)
    if hit:
        return hit
    cands = default_candidates(inp.get("premise", ""), inp.get("scope", "macro"))
    return _put(key, select_factors(inp["job_id"], cands, inp.get("scope", "macro")))


def act_causal(inp: dict) -> dict:
    from app.steps.factors import build_causal_graph, critic_revise
    from app.jobs.repositories import idem_key, idem_get
    from app.db import get_conn
    key = idem_key(inp["job_id"], "build_causal_graph")
    hit = idem_get(key)
    if hit:
        return hit
    with get_conn() as c:
        r = c.execute("SELECT factors FROM factor_sets WHERE job_id=? ORDER BY id DESC LIMIT 1",
                      (inp["job_id"],)).fetchone()
        factors = json.loads(r["factors"]) if r else []
    g = critic_revise(build_causal_graph(inp["job_id"], factors, []))
    return _put(key, g)


def act_init_state(inp: dict) -> dict:
    from app.steps.factors import init_world_state
    from app.jobs.repositories import idem_key, idem_get
    from app.db import get_conn
    key = idem_key(inp["job_id"], "initialize_world_state")
    hit = idem_get(key)
    if hit:
        return hit
    with get_conn() as c:
        r = c.execute("SELECT factors FROM factor_sets WHERE job_id=? ORDER BY id DESC LIMIT 1",
                      (inp["job_id"],)).fetchone()
        factors = json.loads(r["factors"]) if r else []
    return _put(key, {"state": init_world_state(inp["job_id"], factors)})


def _seed_factor_state(factor_ids: list[str]) -> dict:
    """Deterministic seed: each factor starts at 50 (baseline)."""
    return {fid: 50.0 for fid in factor_ids}


def _deterministic_developments(particle: dict, factor_ids: list[str],
                                step: int, step_index: int) -> list[dict]:
    """Deterministic per-particle developments that evolve factor state.

    No LLM. Uses a hash of (particle id, step) to pick which factors drift
    and by how much, so each particle takes a distinct, reproducible path.
    This drives factor-state evolution, branching, and clustering.
    """
    import hashlib
    import math
    seed = hashlib.sha256(f"{particle.get('id', '')}|{step_index}".encode()).digest()
    rng = _make_rng(seed)
    fs = dict(particle.get("factor_state", {}))
    # If no factor set exists (e.g. a recovered job seeded without one), fall back
    # to the particle's own factor keys so state still evolves.
    if not factor_ids:
        factor_ids = list(fs.keys()) or ["generic"]
    n_factors = max(1, len(factor_ids))
    # Number of factor perturbations per step scales with step (more variance later).
    k = min(3, 1 + (step_index % 3))
    devs = []
    for i in range(k):
        fid = factor_ids[rng.randrange(n_factors)]
        # Signed drift: positive/negative, magnitude grows with step.
        mag = 2.0 + 0.5 * (step_index % 10) + rng.uniform(0, 3)
        sign = 1.0 if rng.random() < 0.5 else -1.0
        delta = {fid: sign * mag}
        # Occasionally a larger "event" jump.
        if rng.random() < 0.15:
            delta[fid] += sign * rng.uniform(5, 12)
        prob = 0.5 + rng.uniform(0, 0.4)
        impact = 0.2 + rng.uniform(0, 0.6)
        devs.append({"kind": "drift", "probability": prob, "impact": impact,
                     "delta": delta})
    # Ensure at least one development per particle.
    if not devs:
        devs.append({"kind": "drift", "probability": 0.6, "impact": 0.4,
                     "delta": {factor_ids[0]: 3.0}})
    return devs


def _make_rng(seed: bytes):
    import random
    r = random.Random()
    r.seed(int.from_bytes(seed, "big"))
    return r


def _factor_ids_for_job(jid: str) -> list[str]:
    from app.db import get_conn
    with get_conn() as c:
        r = c.execute("SELECT factors FROM factor_sets WHERE job_id=? ORDER BY id DESC LIMIT 1",
                      (jid,)).fetchone()
    if not r or not r["factors"]:
        return []
    try:
        factors = json.loads(r["factors"])
    except Exception:
        return []
    return [f.get("id") for f in factors if f.get("id")]


def act_loop_step(inp: dict) -> dict:
    """Single loop iteration (continue-as-new unit). Loads checkpoint, runs one step."""
    from app.steps.engine import run_step, stopping_reason
    from app.jobs.repositories import latest_checkpoint, load_budget, store_budget
    jid = inp["job_id"]
    cp = latest_checkpoint(jid)
    step_index = (cp["step_index"] + 1) if cp and cp["phase"] == "projection_loop" else 1
    particles = (cp["payload"].get("particles", None) if cp else None)
    if not particles:
        # First step: seed the initial particle with the factor set values.
        fids = _factor_ids_for_job(jid)
        particles = [{"id": "p0", "factor_state": _seed_factor_state(fids), "probability": 1.0}]
    budget = load_budget(jid)
    fids = _factor_ids_for_job(jid)
    def developments_fn(p: dict) -> list[dict]:
        return _deterministic_developments(p, fids, step_index, step_index)
    out = run_step(jid, step_index, particles, developments_fn=developments_fn,
                   budget=budget)
    store_budget(jid, out["budget"])
    reason = stopping_reason(step_index, out["budget"],
                             horizon_reached=step_index >= budget.get("caps", {}).get("steps", 40))
    out["stop_reason"] = reason
    return out


def act_cluster(inp: dict) -> dict:
    from app.steps.synthesis import synthesize
    from app.jobs.repositories import get_job
    from app.db import get_conn
    jid = inp["job_id"]
    with get_conn() as c:
        parts = [dict(r) for r in c.execute(
            "SELECT id, probability, factor_state FROM particles WHERE job_id=?", (jid,))]
        for p in parts:
            p["factor_state"] = json.loads(p["factor_state"])
    scope = (get_job(jid) or {}).get("scope", "macro")
    outs = synthesize(jid, parts, inp.get("outcome_count", 7),
                      personal={} if scope == "personal" else None)
    return {"outcomes": [o["id"] for o in outs]}


def act_explain(inp: dict) -> dict:
    from app.steps.synthesis import explain
    from app.db import get_conn
    jid = inp["job_id"]
    with get_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM outcomes WHERE job_id=?", (jid,))]
        for r in rows:
            o = {"name": r["name"], "probability": r["probability"], "band": r["band"],
                 "summary": r["summary"]}
            e = explain(o, use_llm=False, job_id=jid)
            c.execute("UPDATE outcomes SET explanation=? WHERE id=?", (e["explanation"], r["id"]))
    return {"explained": len(rows)}


def act_finalize(inp: dict) -> dict:
    from app.store import write_artifact
    from app.jobs.repositories import transition
    from app.db import get_conn
    jid = inp["job_id"]
    with get_conn() as c:
        outs = [dict(r) for r in c.execute("SELECT * FROM outcomes WHERE job_id=?", (jid,))]
    art = write_artifact(jid, "report", {"outcomes": outs})
    try:
        transition(jid, "COMPLETED", "finalized")
    except Exception:
        pass
    return {"artifact": art["id"]}


# ---- workflow (synchronous, in-process) ----
class ProjectionJobWorkflow:
    """Runs the full projection pipeline as a plain Python loop."""

    @staticmethod
    def run_sync(inp: dict) -> dict:
        ctx = dict(inp)
        act_parse(ctx)
        act_factors(ctx)
        act_causal(ctx)
        act_init_state(ctx)
        # Projection loop: checkpoint is the natural boundary.
        # Each act_loop_step call reads the latest checkpoint and advances by 1,
        # so a crash mid-loop resumes from the last saved step, not step 0.
        while True:
            out = act_loop_step(ctx)
            if out.get("stop_reason"):
                break
        act_cluster(ctx)
        act_explain(ctx)
        return act_finalize(ctx)
