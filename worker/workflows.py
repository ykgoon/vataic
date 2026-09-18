"""Temporal workflow + activities (tasks 4.1-4.5). DB writes before completion, idempotent."""
import json
from datetime import timedelta

try:
    from temporalio import workflow, activity
    from temporalio.common import RetryPolicy
    HAS_TEMPORAL = True
except Exception:
    HAS_TEMPORAL = False
    class _D:
        def __getattr__(self, n): return lambda *a, **k: (lambda f: f)
    workflow, activity = _D(), _D()
    RetryPolicy = dict  # type: ignore

PHASES = ["parse_scenario", "extract_axioms", "select_factors", "build_causal_graph",
          "initialize_world_state", "projection_loop", "cluster_outcomes",
          "generate_explanations", "finalize_report"]

RETRY = {"retry_policy": RetryPolicy(maximum_attempts=4,
         initial_interval=timedelta(seconds=2), backoff_coefficient=2.0)} if HAS_TEMPORAL else {}

# ---- activities ----
def _a(name, fn):
    if HAS_TEMPORAL:
        return activity.defn(name=name)(fn)
    fn.__activity_name__ = name
    return fn

def act_parse(inp: dict) -> dict:
    from app.steps.intake import parse_scenario
    from app.jobs.repositories import idem_key, idem_get, idem_put
    key = idem_key(inp["job_id"], "parse_scenario", 0, "", inp.get("premise", "")[:32])
    hit = idem_get(key);  return hit if hit else _put(key, parse_scenario(
        inp["job_id"], inp.get("premise", ""), inp.get("scope", "macro"),
        inp.get("reality_mode", "grounded"), inp.get("horizon", "auto")))

def _put(key: str, res: dict) -> dict:
    from app.jobs.repositories import idem_put
    idem_put(key, res); return res

act_parse = _a("parse_scenario", act_parse)

def act_factors(inp: dict) -> dict:
    from app.steps.factors import default_candidates, select_factors
    from app.jobs.repositories import idem_key, idem_get
    key = idem_key(inp["job_id"], "select_factors", 0, "", inp.get("scope", "macro"))
    hit = idem_get(key)
    if hit: return hit
    cands = default_candidates(inp.get("premise", ""), inp.get("scope", "macro"))
    return _put(key, select_factors(inp["job_id"], cands, inp.get("scope", "macro")))

act_factors = _a("select_factors", act_factors)

def act_causal(inp: dict) -> dict:
    from app.steps.factors import build_causal_graph, critic_revise
    from app.jobs.repositories import idem_key, idem_get
    from app.db import get_conn
    key = idem_key(inp["job_id"], "build_causal_graph")
    hit = idem_get(key)
    if hit: return hit
    with get_conn() as c:
        r = c.execute("SELECT factors FROM factor_sets WHERE job_id=? ORDER BY id DESC LIMIT 1",
                      (inp["job_id"],)).fetchone()
        factors = json.loads(r["factors"]) if r else []
    g = critic_revise(build_causal_graph(inp["job_id"], factors, []))
    return _put(key, g)

act_causal = _a("build_causal_graph", act_causal)

def act_init_state(inp: dict) -> dict:
    from app.steps.factors import init_world_state
    from app.jobs.repositories import idem_key, idem_get
    from app.db import get_conn
    key = idem_key(inp["job_id"], "initialize_world_state")
    hit = idem_get(key)
    if hit: return hit
    with get_conn() as c:
        r = c.execute("SELECT factors FROM factor_sets WHERE job_id=? ORDER BY id DESC LIMIT 1",
                      (inp["job_id"],)).fetchone()
        factors = json.loads(r["factors"]) if r else []
    return _put(key, {"state": init_world_state(inp["job_id"], factors)})

act_init_state = _a("initialize_world_state", act_init_state)

def act_loop_step(inp: dict) -> dict:
    """Single loop iteration (continue-as-new unit). Loads checkpoint, runs one step."""
    from app.steps.engine import run_step, stopping_reason
    from app.jobs.repositories import latest_checkpoint, load_budget, store_budget, transition, get_job
    jid = inp["job_id"]
    cp = latest_checkpoint(jid)
    step = (cp["step_index"] + 1) if cp and cp["phase"] == "projection_loop" else 1
    particles = (cp["payload"].get("particles", [{"id": "p0", "factor_state": {}, "probability": 1.0}])
                 if cp else [{"id": "p0", "factor_state": {}, "probability": 1.0}])
    budget = load_budget(jid)
    out = run_step(jid, step, particles, budget=budget)
    store_budget(jid, out["budget"])
    reason = stopping_reason(step, out["budget"],
                             horizon_reached=step >= budget.get("caps", {}).get("steps", 40))
    out["stop_reason"] = reason
    return out

act_loop_step = _a("projection_loop", act_loop_step)

def act_cluster(inp: dict) -> dict:
    from app.steps.synthesis import synthesize
    from app.jobs.repositories import latest_checkpoint
    from app.db import get_conn
    jid = inp["job_id"]
    cp = latest_checkpoint(jid)
    particles = cp["payload"].get("particles", []) if cp else []
    with get_conn() as c:
        parts = [dict(r) for r in c.execute(
            "SELECT id, probability, factor_state FROM particles WHERE job_id=?", (jid,))]
        for p in parts: p["factor_state"] = json.loads(p["factor_state"])
    particles = parts or particles
    scope = (getattr(__import__("app.jobs.repositories", fromlist=["get_job"]),
                     "get_job")(jid) or {}).get("scope", "macro")
    outs = synthesize(jid, particles, inp.get("outcome_count", 7),
                      personal={} if scope == "personal" else None)
    return {"outcomes": [o["id"] for o in outs]}

act_cluster = _a("cluster_outcomes", act_cluster)

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

act_explain = _a("generate_explanations", act_explain)

def act_finalize(inp: dict) -> dict:
    from app.store import write_artifact
    from app.jobs.repositories import transition
    from app.db import get_conn
    jid = inp["job_id"]
    with get_conn() as c:
        outs = [dict(r) for r in c.execute("SELECT * FROM outcomes WHERE job_id=?", (jid,))]
    art = write_artifact(jid, "report", {"outcomes": outs})
    try: transition(jid, "COMPLETED", "finalized")
    except Exception: pass
    return {"artifact": art["id"]}

act_finalize = _a("finalize_report", act_finalize)

ACTIVITIES = [act_parse, act_factors, act_causal, act_init_state, act_loop_step,
              act_cluster, act_explain, act_finalize]

# ---- workflow (4.1, 4.2) ----
if HAS_TEMPORAL:
    @workflow.defn
    class ProjectionJobWorkflow:
        @workflow.run
        async def run(self, inp: dict) -> dict:
            from app.jobs.repositories import transition as _t
            for phase, fn in [("parse_scenario", act_parse), ("select_factors", act_factors),
                              ("build_causal_graph", act_causal),
                              ("initialize_world_state", act_init_state)]:
                await workflow.execute_activity(fn, inp, start_to_close_timeout=timedelta(minutes=10),
                                                **RETRY)
            # loop-as-reinvocation: iterate steps until stop (continue-as-new pattern)
            while True:
                out = await workflow.execute_activity(
                    act_loop_step, inp, start_to_close_timeout=timedelta(minutes=30), **RETRY)
                if out.get("stop_reason"):
                    break
                if workflow.info().is_continue_as_new_suggested():
                    raise workflow.ContinueAsNewError(inp)
            await workflow.execute_activity(act_cluster, inp,
                start_to_close_timeout=timedelta(minutes=10), **RETRY)
            await workflow.execute_activity(act_explain, inp,
                start_to_close_timeout=timedelta(minutes=10), **RETRY)
            return await workflow.execute_activity(act_finalize, inp,
                start_to_close_timeout=timedelta(minutes=5), **RETRY)

        @staticmethod
        def run_sync(inp: dict) -> dict:
            ctx = dict(inp)
            act_parse(ctx); act_factors(ctx); act_causal(ctx); act_init_state(ctx)
            for _ in range(3):
                out = act_loop_step(ctx)
                if out.get("stop_reason"): break
            act_cluster(ctx); act_explain(ctx)
            return act_finalize(ctx)
else:
    class ProjectionJobWorkflow:  # synchronous fallback for tests without server
        @staticmethod
        def run_sync(inp: dict) -> dict:
            ctx = dict(inp)
            act_parse(ctx); act_factors(ctx); act_causal(ctx); act_init_state(ctx)
            for _ in range(3):
                out = act_loop_step(ctx)
                if out.get("stop_reason"): break
            act_cluster(ctx); act_explain(ctx)
            return act_finalize(ctx)
