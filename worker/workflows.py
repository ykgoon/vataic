"""Projection pipeline: plain in-process activities + run_sync.

No Temporal. The DB state machine, checkpoints, idempotency records, and
leases (in app/jobs/repositories.py) provide all durable-execution
semantics. run_sync() runs the full pipeline as a plain Python loop.
"""
import json
import logging

# ---- activities (plain functions, idempotent) ----
def _put(key: str, res: dict) -> dict:
    from app.jobs.repositories import idem_put
    idem_put(key, res)
    return res


def act_parse(inp: dict) -> dict:
    from app.db import get_conn
    with get_conn() as c:
        c.execute("UPDATE projection_jobs SET current_phase='parse' WHERE id=?", (inp["job_id"],))
    from app.steps.intake import parse_scenario
    from app.jobs.repositories import idem_key, idem_get
    key = idem_key(inp["job_id"], "parse_scenario", 0, "", inp.get("premise", "")[:32])
    hit = idem_get(key)
    return hit if hit else _put(key, parse_scenario(
        inp["job_id"], inp.get("premise", ""), inp.get("scope", "macro"),
        inp.get("reality_mode", "grounded"), inp.get("horizon", "auto"), use_llm=True))


def act_factors(inp: dict) -> dict:
    from app.db import get_conn
    with get_conn() as c:
        c.execute("UPDATE projection_jobs SET current_phase='factors' WHERE id=?", (inp["job_id"],))
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
    with get_conn() as c:
        c.execute("UPDATE projection_jobs SET current_phase='causal' WHERE id=?", (inp["job_id"],))
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
    from app.db import get_conn
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
    with get_conn() as c:
        n_branches = c.execute(
            "SELECT COUNT(*) FROM particles WHERE job_id=? AND parent_id IS NOT NULL",
            (jid,)).fetchone()[0]
    out["budget"]["branches"] = int(n_branches)
    store_budget(jid, out["budget"])
    # Reflect live loop position on the job row (dashboard/detail progress).
    with get_conn() as c:
        c.execute("UPDATE projection_jobs SET current_phase='projection_loop', current_step=? WHERE id=?",
                  (step_index, jid))
    reason = stopping_reason(step_index, out["budget"],
                             horizon_reached=step_index >= budget.get("caps", {}).get("steps", 40))
    out["stop_reason"] = reason
    return out


def act_cluster(inp: dict) -> dict:
    from app.steps.synthesis import synthesize
    from app.jobs.repositories import get_job
    from app.db import get_conn
    jid = inp["job_id"]
    job = get_job(jid) or {}
    with get_conn() as c:
        parts = [dict(r) for r in c.execute(
            "SELECT id, probability, factor_state FROM particles WHERE job_id=?", (jid,))]
        for p in parts:
            p["factor_state"] = json.loads(p["factor_state"])
    scope = job.get("scope", "macro")
    outs = synthesize(jid, parts, inp.get("outcome_count", 7),
                      personal={} if scope == "personal" else None,
                      premise=job.get("premise", ""))
    return {"outcomes": [o["id"] for o in outs]}


def act_explain(inp: dict) -> dict:
    from app.steps.synthesis import explain
    from app.jobs.repositories import get_job
    from app.db import get_conn
    jid = inp["job_id"]
    premise = (get_job(jid) or {}).get("premise", "")
    with get_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM outcomes WHERE job_id=?", (jid,))]
        for r in rows:
            # Pass the full outcome context so the LLM (or narrative fallback)
            # can reason about factor states, not just the name.
            def _j(v):
                try: return json.loads(v) if v else []
                except Exception: return []
            o = {"name": r["name"], "probability": r["probability"], "band": r["band"],
                 "summary": r["summary"],
                 "factor_states": _j(r.get("factor_states")),
                 "indicators": _j(r.get("indicators")),
                 "assumptions": _j(r.get("assumptions")),
                 "risks": _j(r.get("risks")),
                 "causal_path": _j(r.get("causal_path"))}
            e = explain(o, use_llm=True, job_id=jid, premise=premise)
            c.execute("UPDATE outcomes SET explanation=?, indicators=?, assumptions=?, risks=? WHERE id=?",
                      (e.get("explanation", ""), json.dumps(e.get("indicators", [])),
                       json.dumps(e.get("assumptions", [])), json.dumps(e.get("risks", [])), r["id"]))
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
        """Run the full projection pipeline, driving the job state machine
        end-to-end: QUEUED -> INITIALIZING -> RUNNING -> ... -> COMPLETED
        (or FAILED on error).

        The projection loop is the natural checkpoint boundary: each
        act_loop_step reads the latest checkpoint and advances by 1, so a
        crash mid-loop resumes from the last saved step, not step 0.
        """
        from app.jobs.repositories import transition
        jid = inp["job_id"]
        ctx = dict(inp)

        def _to(to_state: str, message: str) -> None:
            try:
                transition(jid, to_state, message)
            except Exception as e:
                # Non-fatal: a transition can be skipped if the job is
                # already in a compatible state (e.g. recovery path).
                logger = logging.getLogger(__name__)
                logger.warning("state transition %s -> %s failed: %s",
                               jid, to_state, e)

        try:
            _to("INITIALIZING", "worker picked up job")
            _to("RUNNING", "starting projection")

            # Pre-loop activities (parse, factors, causal, world state).
            act_parse(ctx)
            act_factors(ctx)
            act_causal(ctx)
            act_init_state(ctx)

            # Projection loop: checkpoint is the natural boundary.
            while True:
                out = act_loop_step(ctx)
                if out.get("stop_reason"):
                    break

            # Post-loop: cluster, explain, finalize.
            _to("CLUSTERING", "loop complete, clustering outcomes")
            act_cluster(ctx)
            _to("EXPLAINING", "explaining outcomes")
            act_explain(ctx)
            from app.llm.usage import reconcile_budget
            reconcile_budget(jid)
            return act_finalize(ctx)

        except Exception as e:
            try:
                transition(jid, "FAILED", f"pipeline error: {e}")
            except Exception:
                pass
            raise
