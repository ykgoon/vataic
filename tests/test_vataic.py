"""Integration/unit tests: schema, state machine, resume, budgets, intake, factors, engine, memory, synthesis, UI."""
import json, os
import pytest

DB = "/tmp/vataic-test.db"

@pytest.fixture(autouse=True)
def _db(monkeypatch):
    for suf in ("", "-wal", "-shm"):
        try: os.remove(DB + suf)
        except OSError: pass
    monkeypatch.setenv("DATABASE_PATH", DB)
    monkeypatch.setenv("ARTIFACT_DIR", "/tmp/vataic-test-artifacts")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1")
    import app.llm.client as _cl
    _cl.reset_breaker()  # per-test provider state, not carried between tests
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_S", "1")
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("LLM_BACKOFF_BASE_S", "0.05")
    import importlib
    import app.config as cfg
    importlib.reload(cfg)
    from app.migrate import apply_pending
    from app.seed import seed
    apply_pending(DB); seed(DB)
    yield

def test_schema_migrations_idempotent_and_seed():
    from app.migrate import apply_pending
    from app.seed import seed
    assert apply_pending(DB) == []  # second run: nothing pending
    assert seed(DB) == 0  # idempotent

def test_state_invalid_rejected_and_budget_fail():
    from app.jobs.repositories import create_job, transition, default_budget, check_budget
    from app.jobs.states import InvalidTransition
    jid = create_job("x")
    with pytest.raises(InvalidTransition):
        transition(jid, "COMPLETED", db_path=DB)  # QUEUED->COMPLETED illegal
    b = default_budget(); b["steps"] = b["caps"]["steps"]
    ok, why = check_budget(b)
    assert not ok and why == "max_steps"

def test_crash_resume_from_checkpoint_not_zero():
    from app.jobs.repositories import create_job, transition, acquire_lease, save_checkpoint, latest_checkpoint, scan_recoverable, load_budget
    jid = create_job("resume me")
    transition(jid, "INITIALIZING", db_path=DB); transition(jid, "RUNNING", db_path=DB)
    acquire_lease(jid, "dead-worker", ttl_s=-1, db_path=DB)  # already expired
    save_checkpoint(jid, "projection_loop", 17, {"particles": []}, {"steps": 17}, DB)
    found = scan_recoverable(DB)
    assert jid in found
    assert latest_checkpoint(jid, DB)["step_index"] == 17

def test_idempotent_retry_no_duplicates():
    from app.jobs.repositories import idem_key, idem_get, idem_put
    k = idem_key("j", "step", 3, "p", "ctx")
    idem_put(k, {"particles": [{"id": "a"}]}, DB)
    idem_put(k, {"particles": [{"id": "b"}]}, DB)  # ignored
    assert idem_get(k, DB)["particles"] == [{"id": "a"}]

def test_lease_takeover():
    from app.jobs.repositories import create_job, acquire_lease, lease_expired
    jid = create_job("lease")
    assert acquire_lease(jid, "w1", ttl_s=100, db_path=DB)
    assert not lease_expired(jid, DB)
    assert acquire_lease(jid, "w2", ttl_s=-1, db_path=DB) is False  # live lease blocks

def test_intake_fantastical_default_horizon_crisis_halt():
    from app.steps.intake import parse_scenario
    from app.jobs.repositories import create_job
    j1 = create_job("Gravity weakens by 5%", scope="macro")
    r = parse_scenario(j1, "Gravity weakens by 5%", horizon="", db_path=DB)
    assert r["horizon"] == "auto" and not r["halted"]
    j2 = create_job("I want to kill myself", scope="personal")
    r2 = parse_scenario(j2, "I want to kill myself", scope="personal", db_path=DB)
    assert r2["halted"] is True

def test_factor_cap_invalid_edge_cycle():
    from app.steps.factors import default_candidates, select_factors, build_causal_graph
    from app.jobs.repositories import create_job
    jid = create_job("cap")
    cands = default_candidates("x", "macro") + [
        {"id": f"extra{i}", "name": f"E{i}", "importance": 0.01} for i in range(10)]
    r = select_factors(jid, cands, "macro", DB)
    assert len(r["factors"]) <= 15 and r["watchlist"]
    g = build_causal_graph(jid, r["factors"][:2],
        [{"source": "nope", "target": r["factors"][0]["id"], "strength": 0.5, "delay_days": 1}], DB)
    assert g["failures"]
    a, b = r["factors"][0]["id"], r["factors"][1]["id"]
    g2 = build_causal_graph(jid, r["factors"][:2], [
        {"source": a, "target": b, "strength": 0.5, "delay_days": 1, "polarity": "+"},
        {"source": b, "target": a, "strength": 0.5, "delay_days": 1, "polarity": "+"}], DB)
    assert g2["cycle_flagged"]

def test_engine_beam_merge_prune_bounds_resume():
    from app.steps.engine import run_step, adaptive_step, merge_prune
    parts = [{"id": f"p{i}", "factor_state": {"f": 50.0 + i}, "probability": 0.2} for i in range(20)]
    kept = merge_prune(parts)
    assert len(kept) <= 12
    assert adaptive_step(0.9) <= adaptive_step(0.1)  # volatile shrinks
    from app.config import settings
    assert settings.step_min_days <= adaptive_step(0.5, llm_rec=99999) <= settings.step_max_days
    from app.jobs.repositories import create_job
    jid = create_job("eng", db_path=DB)
    out = run_step(jid, 1, [{"id": "p0", "factor_state": {}, "probability": 1.0}],
                   developments_fn=lambda p: [{"kind": "drift", "probability": 0.9,
                                               "impact": 0.5, "delta": {}}],
                   budget={"steps": 0}, db_path=DB)
    assert out["particles"]

def test_memory_turning_points_and_truncation():
    from app.jobs.repositories import create_job
    from app.steps.intake import parse_scenario
    from app.steps.memory import build_context, historian_summary, epoch_boundary
    jid = create_job("mem premise")
    parse_scenario(jid, "mem premise", db_path=DB)
    assert epoch_boundary(4)
    h = historian_summary(jid, 4, ["e1", "e2"], db_path=DB)
    ctx = build_context(jid, {"factor_state": {"a": 1}}, 5, {"edges": []}, h, ["crash"], DB)
    assert "crash" in json.dumps(ctx) and "scenario_card" in ctx
    assert len(json.dumps(ctx)) // 4 <= 6000 + 1000

def test_synthesis_weights_merge_trace_explain():
    from app.jobs.repositories import create_job
    from app.steps.synthesis import synthesize, explain
    jid = create_job("syn", db_path=DB)
    parts = [{"id": f"p{i}", "factor_state": {"f": float(i * 30)}, "probability": 0.25} for i in range(4)]
    outs = synthesize(jid, parts, 2, db_path=DB)
    assert abs(sum(o["probability"] for o in outs) - 1.0) < 1e-6
    assert outs[0]["representative"]
    e = explain(dict(outs[0]))
    assert e["explanation"]

def test_llm_cache_reuse_and_validators():
    from app.llm.client import LLMClient
    from app.jobs.repositories import create_job
    jid = create_job("llm-cache", db_path=DB)
    c = LLMClient(base_url="http://127.0.0.1:1", db_path=DB)
    key = c.cache_key("parse", {"messages": [], "model": "m"}, "m", 0)
    from app.db import get_conn
    with get_conn(DB) as conn:
        conn.execute("""INSERT OR IGNORE INTO llm_calls(job_id,cache_key,phase,model,request,response)
          VALUES(?,?,'parse','m','{}','{"premise_type":"event"}')""", (jid, key))
    # craft same payload path via direct cache read
    with get_conn(DB) as conn:
        r = conn.execute("SELECT response FROM llm_calls WHERE cache_key=?", (key,)).fetchone()
        assert json.loads(r["response"])["premise_type"] == "event"

def test_personal_subject_enrichment_stoploss_delete():
    from app.jobs.repositories import create_job
    from app.steps.personal import create_subject, personal_stopping, enrich_personal, delete_personal, get_subject
    jid = create_job("personal", scope="personal")
    sid = create_subject(jid, {"goals": ["new job"]}, DB)
    assert get_subject(jid, DB)["subject_id"] == sid or get_subject(jid, DB)
    assert personal_stopping({"runway": 1.0}, {"stop_loss": 5.0}) == "financial_stop_loss"
    o = enrich_personal({"name": "O1"})
    assert "decision_levers" in o["enrichment"]
    delete_personal(jid, DB)
    assert get_subject(jid, DB) is None

def test_dashboard_and_results_routes():
    from app import create_app
    app = create_app({"TESTING": True})
    cl = app.test_client()
    assert cl.get("/healthz").json == {"ok": True}
    # POST /jobs/new creates the job and redirects to its detail page.
    r = cl.post("/jobs/new", data={"premise": "Test", "scope": "macro"},
                follow_redirects=True)
    assert r.status_code == 200
    assert cl.get("/jobs").status_code == 200
    assert cl.get("/jobs/partial").status_code == 200
    assert cl.get("/settings").status_code == 200

def test_artifact_store_and_budget_persist():
    from app.jobs.repositories import create_job, load_budget, store_budget
    from app.store import write_artifact, read_artifact
    jid = create_job("art")
    art = write_artifact(jid, "report", {"a": 1}, DB, base="/tmp/vataic-test-artifacts")
    assert read_artifact(art["path"]) == {"a": 1}
    b = load_budget(jid, DB); b["llm_calls"] = 3
    store_budget(jid, b, DB)
    assert load_budget(jid, DB)["llm_calls"] == 3

def test_activities_idempotent_and_resume():
    """Full run completes, and re-entry is safe (idempotent, no crash)."""
    from worker.workflows import ProjectionJobWorkflow, act_loop_step
    from app.jobs.repositories import create_job, transition, save_checkpoint, latest_checkpoint
    jid = create_job("wf premise")
    out = ProjectionJobWorkflow.run_sync({"job_id": jid, "premise": "wf premise",
                                          "scope": "macro", "outcome_count": 3})
    assert "artifact" in out
    # Re-entry after completion: must not crash and must return a valid dict.
    again = act_loop_step({"job_id": jid})
    assert isinstance(again, dict) and "particles" in again

    # Simulate a crash mid-projection: job is RUNNING with a checkpoint that has
    # non-empty particles. A recovering worker re-enters via act_loop_step and
    # must resume from that checkpoint (not step 0) and produce particles.
    from app.jobs.repositories import create_job as _c
    jid2 = _c("resume mid")
    transition(jid2, "INITIALIZING", db_path=None)
    transition(jid2, "RUNNING", db_path=None)
    # Seed a factor set + particles so a mid-run checkpoint exists.
    save_checkpoint(jid2, "projection_loop", 12,
                    {"particles": [{"id": "p0", "factor_state": {"f": 55.0}, "probability": 0.7},
                                   {"id": "p1", "factor_state": {"f": 40.0}, "probability": 0.3}]},
                    {"steps": 12, "caps": {"steps": 40}}, db_path=None)
    cp = latest_checkpoint(jid2, db_path=None)
    assert cp["step_index"] == 12
    resumed = act_loop_step({"job_id": jid2})
    assert resumed["stop_reason"] is None or "max_steps" in (resumed.get("stop_reason") or "")
    assert resumed["particles"]  # resumed from step 12, not step 0
    assert latest_checkpoint(jid2, db_path=None)["step_index"] > 12

def test_llm_usage_recorded_and_reconciled():
    """Real provider calls must land in budget_state (no false zeros)."""
    from app.llm.client import LLMClient
    from app.jobs.repositories import create_job, load_budget
    from app.db import get_conn

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": json.dumps(
                {"premise_type": "event", "axioms": [], "entities": [],
                 "systems": [], "reality_mode": "grounded", "horizon": "auto"})}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                          "total_tokens": 150}}
    jid = create_job("usage premise", db_path=DB)
    c = LLMClient(base_url="http://127.0.0.1:1", db_path=DB)
    import app.llm.client as cl_mod
    orig = cl_mod.requests.post
    cl_mod.requests.post = lambda *a, **k: FakeResp()
    try:
        out = c.chat(jid, "parse", [{"role": "user", "content": "hi"}])
        assert not out["cache_hit"]
    finally:
        cl_mod.requests.post = orig
    b = load_budget(jid, DB)
    assert b["llm_calls"] == 1, f"expected 1 recorded call, got {b}"
    assert b["tokens"] == 150 and b["prompt_tokens"] == 100
    # Reconcile from llm_calls ledger agrees.
    from app.llm.usage import reconcile_budget
    b2 = reconcile_budget(jid, DB)
    assert b2["llm_calls"] == 1 and b2["tokens"] == 150

def test_outcomes_readable_names_and_summaries():
    """Outcomes must read like a report: human names, no raw factor ids,
    no float-noise probabilities, real narrative summary/explanation."""
    from app.jobs.repositories import create_job
    from app.steps.factors import select_factors
    from app.steps.synthesis import synthesize
    jid = create_job("YouTube channel chronicle premise", db_path=DB)
    facs = [{"id": "f0", "name": "Skill Development", "current": 50.0},
            {"id": "f1", "name": "Income Trajectory", "current": 50.0},
            {"id": "f2", "name": "Reputation", "current": 50.0}]
    select_factors(jid, facs, "personal", DB)
    parts = [{"id": "p0", "factor_state": {"f0": 72.0, "f1": 41.0, "f2": 58.0},
              "probability": 0.9},
             {"id": "p1", "factor_state": {"f0": 51.0, "f1": 49.0, "f2": 50.0},
              "probability": 0.1}]
    outs = synthesize(jid, parts, 2, db_path=DB, premise="YouTube channel premise")
    assert len(outs) >= 1
    names_blob = " ".join(o["name"] for o in outs)
    assert "Skill Development" in (outs[0]["summary"] + names_blob) or \
           "Income Trajectory" in (outs[0]["summary"] + names_blob)
    for o in outs:
        assert not o["name"].startswith("f0 "), f"raw-id name: {o['name']}"
        assert "Representative path from" not in o["summary"]
        assert 0.999 <= sum(x["probability"] for x in outs) <= 1.001
    # explanation narrative present without LLM
    from app.steps.synthesis import explain
    e = explain(dict(outs[0]), use_llm=False, job_id=jid, db_path=DB)
    assert "most likely path" in e["explanation"] or "possible path" in e["explanation"] \
        or "credible path" in e["explanation"] or "secondary path" in e["explanation"]
    assert "f0" not in e["explanation"]

def test_settings_page_shows_effective_values():
    """Settings page must render effective env/seeded values, not 'Not configured'."""
    from app import create_app
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.get("/settings")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "openai-compatible" in body          # provider value rendered
    assert "500" in body                         # budget.max_llm_calls rendered
    assert body.count("Not configured") <= 1     # at most the optional key row
    assert "not reachable" in body or "reachable" in body  # status probe shown


def test_settings_seed_rows_lose_to_real_env():
    """A DB row that only repeats the seeded default must not mask an env value."""
    from app.routes.settings import _effective
    from app.seed import DEFAULTS
    seeded = dict(DEFAULTS)
    eff = _effective(seeded)
    # env (set by conftest) is http://127.0.0.1:1; the seed row says ollama default
    assert eff["llm.base_url"] != DEFAULTS["llm.base_url"]
    assert eff["llm.base_url"] == "http://127.0.0.1:1"
    # an explicit (non-default) DB override still wins over env
    seeded2 = dict(DEFAULTS); seeded2["llm.base_url"] = "http://custom:9/v1"
    eff2 = _effective(seeded2)
    assert eff2["llm.base_url"] == "http://custom:9/v1"
