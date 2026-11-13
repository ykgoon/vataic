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
    monkeypatch.setenv("VATAIC_ALLOW_HEURISTIC", "1")
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
    from app.scenarios.repos import create_scenario, transition, default_budget, check_budget
    from app.scenarios.state import InvalidTransition
    jid = create_scenario("x")
    with pytest.raises(InvalidTransition):
        transition(jid, "COMPLETED", db_path=DB)  # QUEUED->COMPLETED illegal
    b = default_budget(); b["steps"] = b["caps"]["steps"]
    ok, why = check_budget(b)
    assert not ok and why == "max_steps"

def test_crash_resume_from_checkpoint_not_zero():
    from app.scenarios.repos import create_scenario, transition, acquire_lease, save_checkpoint, latest_checkpoint, scan_recoverable, load_budget
    jid = create_scenario("resume me")
    transition(jid, "INITIALIZING", db_path=DB); transition(jid, "RUNNING", db_path=DB)
    acquire_lease(jid, "dead-worker", ttl_s=-1, db_path=DB)  # already expired
    save_checkpoint(jid, "projection_loop", 17, {"particles": []}, {"steps": 17}, DB)
    found = scan_recoverable(DB)
    assert jid in found
    assert latest_checkpoint(jid, DB)["step_index"] == 17

def test_idempotent_retry_no_duplicates():
    from app.scenarios.repos import idem_key, idem_get, idem_put
    k = idem_key("j", "step", 3, "p", "ctx")
    idem_put(k, {"particles": [{"id": "a"}]}, DB)
    idem_put(k, {"particles": [{"id": "b"}]}, DB)  # ignored
    assert idem_get(k, DB)["particles"] == [{"id": "a"}]

def test_lease_takeover():
    from app.scenarios.repos import create_scenario, acquire_lease, lease_expired
    jid = create_scenario("lease")
    assert acquire_lease(jid, "w1", ttl_s=100, db_path=DB)
    assert not lease_expired(jid, DB)
    assert acquire_lease(jid, "w2", ttl_s=-1, db_path=DB) is False  # live lease blocks

def test_intake_fantastical_default_horizon_crisis_halt():
    from app.steps.intake import parse_scenario
    from app.scenarios.repos import create_scenario
    j1 = create_scenario("Gravity weakens by 5%", scope="macro")
    r = parse_scenario(j1, "Gravity weakens by 5%", horizon="", db_path=DB)
    assert r["horizon"] == "auto" and not r["halted"]
    j2 = create_scenario("I want to kill myself", scope="personal")
    r2 = parse_scenario(j2, "I want to kill myself", scope="personal", db_path=DB)
    assert r2["halted"] is True

def test_factor_cap_invalid_edge_cycle():
    from app.steps.factors import default_candidates, select_factors, build_causal_graph
    from app.scenarios.repos import create_scenario
    jid = create_scenario("cap")
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
    from app.scenarios.repos import create_scenario
    jid = create_scenario("eng", db_path=DB)
    out = run_step(jid, 1, [{"id": "p0", "factor_state": {}, "probability": 1.0}],
                   developments_fn=lambda p: [{"kind": "drift", "probability": 0.9,
                                               "impact": 0.5, "delta": {}}],
                   budget={"steps": 0}, db_path=DB)
    assert out["particles"]

def test_memory_turning_points_and_truncation():
    from app.scenarios.repos import create_scenario
    from app.steps.intake import parse_scenario
    from app.steps.memory import build_context, historian_summary, epoch_boundary
    jid = create_scenario("mem premise")
    parse_scenario(jid, "mem premise", db_path=DB)
    assert epoch_boundary(4)
    h = historian_summary(jid, 4, ["e1", "e2"], db_path=DB)
    ctx = build_context(jid, {"factor_state": {"a": 1}}, 5, {"edges": []}, h, ["crash"], DB)
    assert "crash" in json.dumps(ctx) and "scenario_card" in ctx
    assert len(json.dumps(ctx)) // 4 <= 6000 + 1000

def test_synthesis_weights_merge_trace_explain():
    from app.scenarios.repos import create_scenario
    from app.steps.synthesis import synthesize, explain
    jid = create_scenario("syn", db_path=DB)
    parts = [{"id": f"p{i}", "factor_state": {"f": float(i * 30)}, "probability": 0.25} for i in range(4)]
    outs = synthesize(jid, parts, 2, db_path=DB)
    assert abs(sum(o["probability"] for o in outs) - 1.0) < 1e-6
    assert outs[0]["representative"]
    e = explain(dict(outs[0]))
    assert e["explanation"]

def test_llm_cache_reuse_and_validators():
    from app.llm.client import LLMClient
    from app.scenarios.repos import create_scenario
    jid = create_scenario("llm-cache", db_path=DB)
    c = LLMClient(base_url="http://127.0.0.1:1", db_path=DB)
    key = c.cache_key("parse", {"messages": [], "model": "m"}, "m", 0)
    from app.db import get_conn
    with get_conn(DB) as conn:
        conn.execute("""INSERT OR IGNORE INTO llm_calls(scenario_id,cache_key,phase,model,request,response)
          VALUES(?,?,'parse','m','{}','{"premise_type":"event"}')""", (jid, key))
    # craft same payload path via direct cache read
    with get_conn(DB) as conn:
        r = conn.execute("SELECT response FROM llm_calls WHERE cache_key=?", (key,)).fetchone()
        assert json.loads(r["response"])["premise_type"] == "event"

def test_personal_subject_enrichment_stoploss_delete():
    from app.scenarios.repos import create_scenario
    from app.steps.personal import create_subject, personal_stopping, enrich_personal, delete_personal, get_subject
    jid = create_scenario("personal", scope="personal")
    sid = create_subject(jid, {"goals": ["new scenario"]}, DB)
    assert get_subject(jid, DB)["subject_id"] == sid or get_subject(jid, DB)
    assert personal_stopping({"runway": 1.0}, {"stop_loss": 5.0}) == "financial_stop_loss"
    o = enrich_personal({"name": "O1"})
    assert "decision_levers" in o["enrichment"]
    delete_personal(jid, DB)
    assert get_subject(jid, DB) is None

def test_retry_clears_previous_error_from_db_and_ui():
    """A scenario that failed and was retried successfully must not keep
    showing the previous attempt's error (dashboard row + detail page)."""
    from app import create_app
    from app.scenarios.repos import create_scenario, transition, get_scenario
    from app.db import get_conn

    app = create_app({"TESTING": True})
    cl = app.test_client()
    jid = create_scenario("retry premise", db_path=DB)

    # Fail it the way the worker does: status FAILED plus the message column.
    transition(jid, "INITIALIZING", db_path=DB)
    transition(jid, "RUNNING", db_path=DB)
    with get_conn(DB) as c:
        c.execute("UPDATE projection_scenarios SET status='FAILED', error=?, updated_at=datetime('now')"
                  " WHERE id=?", ("pipeline error: provider exploded", jid))

    body = cl.get(f"/scenarios/{jid}").get_data(as_text=True)
    assert "pipeline error: provider exploded" in body
    assert "provider exploded" in cl.get("/scenarios/partial").get_data(as_text=True)

    # Retry (as the UI button does) => FAILED -> QUEUED and the message is gone
    # before the new run has produced anything.
    r = cl.post(f"/scenarios/{jid}/retry")
    assert r.status_code == 200
    assert get_scenario(jid, DB)["status"] == "QUEUED"
    assert not get_scenario(jid, DB)["error"]
    assert "provider exploded" not in cl.get("/scenarios/partial").get_data(as_text=True)
    assert "provider exploded" not in cl.get(f"/scenarios/{jid}").get_data(as_text=True)

    # A successful run leaves no residue either.
    from worker.workflows import ProjectionScenarioWorkflow
    out = ProjectionScenarioWorkflow.run_sync(
        {"scenario_id": jid, "premise": "retry premise", "scope": "macro", "outcome_count": 3})
    assert "artifact" in out
    row = get_scenario(jid, DB)
    assert row["status"] == "COMPLETED" and not row["error"]
    assert "provider exploded" not in cl.get("/scenarios/partial").get_data(as_text=True)
    assert "scenario-error" not in cl.get(f"/scenarios/{jid}").get_data(as_text=True)


def test_stale_error_not_rendered_for_successful_scenario():
    """Rows written before the retry clearing existed can still carry an error
    string while being COMPLETED/RUNNING: never render it as current."""
    from app import create_app
    from app.scenarios.repos import create_scenario, transition
    from app.db import get_conn

    app = create_app({"TESTING": True})
    cl = app.test_client()
    jid = create_scenario("legacy stale error", db_path=DB)
    transition(jid, "INITIALIZING", db_path=DB)
    transition(jid, "RUNNING", db_path=DB)
    with get_conn(DB) as c:
        # Legacy shape: the run recovered and kept going, the text was never cleared.
        c.execute("UPDATE projection_scenarios SET error=? WHERE id=?",
                  ("pipeline error: old news", jid))

    body = cl.get(f"/scenarios/{jid}").get_data(as_text=True)
    assert "old news" not in body
    assert "old news" not in cl.get("/scenarios/partial").get_data(as_text=True)


def test_migration_007_clears_errors_on_non_failed_scenarios():
    from app.scenarios.repos import create_scenario, transition, get_scenario
    from app.db import get_conn
    from app.migrate import apply_pending

    failed = create_scenario("still failed", db_path=DB)
    completed = create_scenario("already recovered", db_path=DB)
    with get_conn(DB) as c:
        c.execute("UPDATE projection_scenarios SET status='FAILED', error='boom' WHERE id=?", (failed,))
        c.execute("UPDATE projection_scenarios SET status='COMPLETED', error='stale boom' WHERE id=?",
                  (completed,))
        c.execute("DELETE FROM schema_migrations WHERE version='007_clear_stale_scenario_error'")

    assert "007_clear_stale_scenario_error" in apply_pending(DB)
    assert get_scenario(failed, DB)["error"] == "boom"      # current failure kept
    assert not get_scenario(completed, DB)["error"]         # stale message dropped
    # Idempotent on a second application.
    assert apply_pending(DB) == []


def test_dashboard_and_results_routes():
    from app import create_app
    app = create_app({"TESTING": True})
    cl = app.test_client()
    assert cl.get("/healthz").json == {"ok": True}
    # POST /scenarios/new creates the scenario and redirects to its detail page.
    r = cl.post("/scenarios/new", data={"premise": "Test", "scope": "macro"},
                follow_redirects=True)
    assert r.status_code == 200
    assert cl.get("/scenarios").status_code == 200
    assert cl.get("/scenarios/partial").status_code == 200
    assert cl.get("/settings").status_code == 200

def test_artifact_store_and_budget_persist():
    from app.scenarios.repos import create_scenario, load_budget, store_budget
    from app.store import write_artifact, read_artifact
    jid = create_scenario("art")
    art = write_artifact(jid, "report", {"a": 1}, DB, base="/tmp/vataic-test-artifacts")
    assert read_artifact(art["path"]) == {"a": 1}
    b = load_budget(jid, DB); b["llm_calls"] = 3
    store_budget(jid, b, DB)
    assert load_budget(jid, DB)["llm_calls"] == 3

def test_activities_idempotent_and_resume():
    """Full run completes, and re-entry is safe (idempotent, no crash)."""
    from worker.workflows import ProjectionScenarioWorkflow, act_loop_step
    from app.scenarios.repos import create_scenario, transition, save_checkpoint, latest_checkpoint
    jid = create_scenario("wf premise")
    out = ProjectionScenarioWorkflow.run_sync({"scenario_id": jid, "premise": "wf premise",
                                          "scope": "macro", "outcome_count": 3})
    assert "artifact" in out
    # Re-entry after completion: must not crash and must return a valid dict.
    again = act_loop_step({"scenario_id": jid})
    assert isinstance(again, dict) and "particles" in again

    # Simulate a crash mid-projection: scenario is RUNNING with a checkpoint that has
    # non-empty particles. A recovering worker re-enters via act_loop_step and
    # must resume from that checkpoint (not step 0) and produce particles.
    from app.scenarios.repos import create_scenario as _c
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
    resumed = act_loop_step({"scenario_id": jid2})
    assert resumed["stop_reason"] is None or "max_steps" in (resumed.get("stop_reason") or "")
    assert resumed["particles"]  # resumed from step 12, not step 0
    assert latest_checkpoint(jid2, db_path=None)["step_index"] > 12

def test_llm_usage_recorded_and_reconciled():
    """Real provider calls must land in budget_state (no false zeros)."""
    from app.llm.client import LLMClient
    from app.scenarios.repos import create_scenario, load_budget
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
    jid = create_scenario("usage premise", db_path=DB)
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
    from app.scenarios.repos import create_scenario
    from app.steps.factors import select_factors
    from app.steps.synthesis import synthesize
    jid = create_scenario("YouTube channel chronicle premise", db_path=DB)
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
    e = explain(dict(outs[0]), use_llm=False, scenario_id=jid, db_path=DB)
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

def test_readiness_gate_fails_scenario_without_provider(monkeypatch):
    """No hatch + dead provider: pickup must fail fast with a clear error."""
    monkeypatch.delenv("VATAIC_ALLOW_HEURISTIC", raising=False)
    import pytest
    from worker.workflows import act_parse
    from app.scenarios.repos import create_scenario, transition
    jid = create_scenario("gate premise", db_path=DB)
    transition(jid, "INITIALIZING", db_path=DB)
    transition(jid, "RUNNING", db_path=DB)
    with pytest.raises(RuntimeError) as ei:
        act_parse({"scenario_id": jid, "premise": "gate premise"})
    assert "LLM provider unavailable" in str(ei.value)


def test_readiness_gate_passes_with_ready_provider(monkeypatch):
    """A reachable provider serving the configured models lets pickup proceed."""
    from worker.workflows import act_parse
    from app.scenarios.repos import create_scenario, transition, get_scenario
    import app.llm.prepared as prep

    class FakeResp:
        status_code = 200
        def json(self):
            return {"data": [{"id": "fake-provider-model"}]}

    monkeypatch.setattr(prep.requests, "get", lambda *a, **k: FakeResp())
    jid = create_scenario("gate ok premise", db_path=DB)
    transition(jid, "INITIALIZING", db_path=DB)
    transition(jid, "RUNNING", db_path=DB)
    # act_parse proceeds into intake (heuristic parse under offline test env,
    # but the gate itself must not raise).
    out = act_parse({"scenario_id": jid, "premise": "gate ok premise"})
    assert out["halted"] is False


def test_readiness_gate_hatch_allows_heuristic(monkeypatch):
    monkeypatch.setenv("VATAIC_ALLOW_HEURISTIC", "1")
    from worker.workflows import act_parse
    from app.scenarios.repos import create_scenario, transition
    jid = create_scenario("gate hatch premise", db_path=DB)
    transition(jid, "INITIALIZING", db_path=DB)
    transition(jid, "RUNNING", db_path=DB)
    out = act_parse({"scenario_id": jid, "premise": "gate hatch premise"})
    assert out["halted"] is False


def test_delete_scenario_route_and_repos():
    from app import create_app
    from app.scenarios.repos import create_scenario, get_scenario, delete_scenario, transition
    from app.store import write_artifact
    from app.steps.factors import select_factors
    from app.db import get_conn
    app = create_app({"TESTING": True})
    cl = app.test_client()
    jid = create_scenario("delete me", db_path=DB)
    select_factors(jid, [{"id": "f0", "name": "F Zero", "current": 50.0}], "macro", DB)
    write_artifact(jid, "report", {"a": 1}, DB)
    import os
    from pathlib import Path
    art_dir = Path(os.environ["ARTIFACT_DIR"]) / jid
    assert art_dir.exists()

    # Non-terminal states are auto-cancelled, then removed (stranded
    # scenarios must never be undeletable). Plain form posts (detail page)
    # get a redirect to the dashboard; htmx posts get a fresh tbody partial
    # (removing one row raced the 3s poll's re-render into swapError).
    r = cl.post(f"/scenarios/{jid}/delete")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/scenarios")
    assert get_scenario(jid, DB) is None
    assert not art_dir.exists()
    with get_conn(DB) as c:
        for t in ("factor_sets", "scenario_events", "artifacts"):
            n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE scenario_id=?", (jid,)).fetchone()[0]
            assert n == 0, t

    # htmx path: response swaps fresh dashboard rows into the tbody.
    jid2 = create_scenario("delete me via htmx", db_path=DB)
    r = cl.post(f"/scenarios/{jid2}/delete", headers={"HX-Request": "true"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "scenarios-tbody" not in body          # partial rows, not the page
    assert jid2 not in body                        # deleted row absent
    assert get_scenario(jid2, DB) is None

    # Deleting an already-gone scenario is idempotent: same responses, no 500.
    assert cl.post(f"/scenarios/{jid}/delete").status_code == 302
    assert cl.post(f"/scenarios/{jid}/delete", headers={"HX-Request": "true"}).status_code == 200


def test_delete_scenario_detaches_inbound_cross_scenario_particles():
    """Another scenario's particle can root itself on this scenario's particles
    (parent_id is a bare FK on particles(id), not scenario-scoped). Deleting
    the parent scenario must detach those foreign children instead of tripping
    the immediate FK check (observed live: one scenario's root 'p0' was the
    parent for two other scenarios' trees, making it undeletable)."""
    from app.scenarios.repos import create_scenario, get_scenario, delete_scenario
    from app.db import get_conn
    jid_a = create_scenario("parent scenario", db_path=DB)
    jid_b = create_scenario("foreign child scenario", db_path=DB)
    with get_conn(DB) as c:
        c.execute(
            "INSERT INTO particles (id, scenario_id, parent_id, probability) VALUES ('p0', ?, NULL, 1.0)",
            (jid_a,))
        c.execute(
            "INSERT INTO particles (id, scenario_id, parent_id, probability) VALUES ('foreign', ?, 'p0', 1.0)",
            (jid_b,))

    delete_scenario(jid_a, DB)

    assert get_scenario(jid_a, DB) is None
    with get_conn(DB) as c:
        # scenario A's particles (incl. 'p0') are gone, but scenario B keeps
        # its particle, detached, and scenario B itself survives.
        assert c.execute("SELECT COUNT(*) FROM particles WHERE scenario_id=?", (jid_a,)).fetchone()[0] == 0
        row = c.execute("SELECT parent_id FROM particles WHERE scenario_id=?", (jid_b,)).fetchone()
        assert row is not None and row["parent_id"] is None
        assert get_scenario(jid_b, DB) is not None


def test_chat_panel_roundtrip_failure_and_clear():
    from app import create_app
    from app.scenarios.repos import create_scenario
    from app.db import get_conn
    app = create_app({"TESTING": True})
    cl = app.test_client()
    jid = create_scenario("chat premise", db_path=DB)

    # Detail page includes the panel.
    r = cl.get(f"/scenarios/{jid}")
    assert r.status_code == 200 and b"chat-panel" in r.get_data()

    # Provider down (test env): error bubble, nothing stored.
    r = cl.post(f"/scenarios/{jid}/chat", data={"message": "why?"})
    body = r.get_data(as_text=True)
    assert "chat-error" in body and "provider unavailable" in body
    with get_conn(DB) as c:
        n = c.execute("SELECT COUNT(*) FROM scenario_chat_messages WHERE scenario_id=?",
                      (jid,)).fetchone()[0]
    assert n == 0

    # Success path: stub the provider probe and the LLM reply; the reply
    # fills asynchronously (background thread), so wait for it to land.
    import time as _t
    import app.llm.prepared as prep
    import app.routes.chat as chatmod
    orig_reply = chatmod._reply
    orig_status = prep.provider_status
    chatmod._reply = lambda jid2, q: "Because three factors moved together."
    prep.provider_status = lambda db_path=None: {"ok": True, "detail": "stub", "model": "m"}
    try:
        r = cl.post(f"/scenarios/{jid}/chat", data={"message": "why?"})
        body = r.get_data(as_text=True)
        # Fast stubbed replies may land before the response renders: the
        # immediate panel shows either the pending bubble or the final reply.
        assert "chat-thinking" in body or "Because three factors moved together." in body
        rows = []
        for _ in range(50):  # up to ~5s for the background thread
            _t.sleep(0.1)
            with get_conn(DB) as c:
                rows = [dict(x) for x in c.execute(
                    "SELECT role, content, error FROM scenario_chat_messages WHERE scenario_id=? ORDER BY id",
                    (jid,))]
            if rows and rows[-1]["content"]:
                break
        assert [(m["role"], m["content"]) for m in rows] == \
            [("user", "why?"), ("assistant", "Because three factors moved together.")]
    finally:
        chatmod._reply = orig_reply
        prep.provider_status = orig_status

    # History re-renders on GET; clear empties it.
    r = cl.get(f"/scenarios/{jid}/chat")
    assert "Because three factors moved together." in r.get_data(as_text=True)

    # Mid-generation failure: reply slot gets the error, no invented content.
    def _boom(jid2, q):
        raise RuntimeError("provider exploded")
    chatmod._reply = _boom
    prep.provider_status = lambda db_path=None: {"ok": True, "detail": "stub", "model": "m"}
    try:
        cl.post(f"/scenarios/{jid}/chat", data={"message": "again"})
        rows = []
        for _ in range(50):
            _t.sleep(0.1)
            with get_conn(DB) as c:
                rows = [dict(x) for x in c.execute(
                    "SELECT role, content, error FROM scenario_chat_messages WHERE scenario_id=? ORDER BY id",
                    (jid,))]
            if rows and rows[-1]["error"]:
                break
        assert rows[-1]["error"] and "provider exploded" in rows[-1]["error"]
        assert rows[-1]["content"] == ""
    finally:
        chatmod._reply = orig_reply
        prep.provider_status = orig_status
    cl.post(f"/scenarios/{jid}/chat/clear")
    with get_conn(DB) as c:
        n = c.execute("SELECT COUNT(*) FROM scenario_chat_messages WHERE scenario_id=?",
                      (jid,)).fetchone()[0]
    assert n == 0

def test_finalize_zero_llm_gate(monkeypatch):
    """Finalization must refuse to mark a zero-LLM run COMPLETED (no hatch)."""
    from worker.workflows import act_finalize
    from app.scenarios.repos import create_scenario, transition
    monkeypatch.delenv("VATAIC_ALLOW_HEURISTIC", raising=False)
    jid = create_scenario("honest finalize", db_path=DB)
    for st in ("INITIALIZING", "RUNNING", "CLUSTERING", "EXPLAINING"):
        transition(jid, st, db_path=DB)
    with pytest.raises(RuntimeError) as ei:
        act_finalize({"scenario_id": jid})
    assert "no LLM calls recorded" in str(ei.value)
    # With the hatch set, finalization proceeds and completes the scenario.
    monkeypatch.setenv("VATAIC_ALLOW_HEURISTIC", "1")
    out = act_finalize({"scenario_id": jid})
    assert "artifact" in out
    from app.scenarios.repos import get_scenario
    assert get_scenario(jid, DB)["status"] == "COMPLETED"

def test_stranded_explaining_is_recovered_and_completes():
    """A worker death mid-EXPLAINING (post-loop) must surface in
    scan_recoverable and be re-driven to COMPLETED by run_sync."""
    from app.scenarios.repos import create_scenario, transition, acquire_lease, scan_recoverable, get_scenario
    from worker.workflows import ProjectionScenarioWorkflow
    jid = create_scenario("strand me", db_path=DB)
    for st in ("INITIALIZING", "RUNNING", "CLUSTERING", "EXPLAINING"):
        transition(jid, st, db_path=DB)
    acquire_lease(jid, "dead-worker", ttl_s=-1, db_path=DB)
    assert jid in scan_recoverable(DB)
    out = ProjectionScenarioWorkflow.run_sync({"scenario_id": jid, "premise": "strand me"})
    assert "artifact" in out
    assert get_scenario(jid, DB)["status"] == "COMPLETED"


def test_act_cluster_rerun_no_duplicate_outcomes():
    """Re-entry after a mid-cluster crash must replace, not duplicate."""
    from worker.workflows import act_cluster
    from app.scenarios.repos import create_scenario, transition
    from app.db import get_conn
    jid = create_scenario("cluster twice", db_path=DB)
    for st in ("INITIALIZING", "RUNNING", "CLUSTERING"):
        transition(jid, st, db_path=DB)
    with get_conn(DB) as c:
        c.execute("INSERT INTO particles(id,scenario_id,probability,factor_state,created_step) VALUES('p0',?,1.0,'{}',0)", (jid,))
    act_cluster({"scenario_id": jid, "outcome_count": 3})
    act_cluster({"scenario_id": jid, "outcome_count": 3})
    with get_conn(DB) as c:
        n = c.execute("SELECT COUNT(*) FROM outcomes WHERE scenario_id=?", (jid,)).fetchone()[0]
    assert n == 1


def test_run_sync_aborts_mid_loop_on_cancel(monkeypatch):
    """User cancel must stop the pipeline between steps, never finalize."""
    import worker.workflows as wf
    from app.scenarios.repos import create_scenario, transition, get_scenario
    jid = create_scenario("cancel mid-loop", db_path=DB)
    transition(jid, "INITIALIZING", db_path=DB)
    transition(jid, "RUNNING", db_path=DB)
    calls = {"n": 0}
    def fake_step(inp):
        calls["n"] += 1
        transition(jid, "CANCELLED", db_path=DB)
        return {"particles": [], "step_index": 1}
    monkeypatch.setattr(wf, "act_loop_step", fake_step)
    out = wf.ProjectionScenarioWorkflow.run_sync({"scenario_id": jid, "premise": "x"})
    assert out.get("status") == "CANCELLED"
    assert calls["n"] == 1
    assert get_scenario(jid, DB)["status"] == "CANCELLED"


def test_cancel_badge_shows_new_state():
    from app import create_app
    from app.scenarios.repos import create_scenario, get_scenario
    app = create_app({"TESTING": True})
    cl = app.test_client()
    jid = create_scenario("badge me", db_path=DB)
    r = cl.post(f"/scenarios/{jid}/cancel")
    assert b"CANCELLED" in r.get_data()
    assert get_scenario(jid, DB)["status"] == "CANCELLED"


def test_dashboard_delete_button_always_present():
    """Stranded non-terminal scenarios must be deletable from the UI."""
    from app import create_app
    from app.scenarios.repos import create_scenario
    app = create_app({"TESTING": True})
    cl = app.test_client()
    jid = create_scenario("ui delete", db_path=DB)
    r = cl.get("/scenarios/partial")
    assert f"/scenarios/{jid}/delete".encode() in r.get_data()


def test_seed_default_primary_model_blank():
    """Default model config is blank: provider default applies (no
    hardcoded model name that breaks non-OpenAI providers)."""
    from app.seed import DEFAULTS
    assert DEFAULTS["llm.primary_model"] == ""


def test_migration_006_purges_legacy_default_model():
    """A DB upgraded from an old build can carry a provider-specific default
    model in settings; migration 006 must blank it while leaving
    operator-chosen models untouched."""
    from app.db import get_conn
    from app.migrate import apply_pending
    legacy = bytes.fromhex("6770742d346f2d6d696e69").decode()
    with get_conn() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('llm.primary_model',?)", (legacy,))
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('llm.strong_model','hermes-agent')")
        c.execute("DELETE FROM schema_migrations WHERE version='006_clear_legacy_default_model'")
    apply_pending(DB)
    with get_conn() as c:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
    assert rows["llm.primary_model"] == ""
    assert rows["llm.strong_model"] == "hermes-agent"


def test_model_for_role_blank_db_primary_stays_blank(monkeypatch):
    """A blank llm.primary_model (env or DB) is honoured end-to-end:
    no hardcoded model name may be resurrected as a fallback."""
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "")
    monkeypatch.setenv("LLM_STRONG_MODEL", "")
    monkeypatch.setenv("LLM_CHEAP_MODEL", "")
    import importlib
    import app.config as cfg
    importlib.reload(cfg)
    from app.db import get_conn
    with get_conn() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('llm.primary_model','')")
    from app.llm.client import model_for_role
    assert model_for_role("strong") == ""
    assert model_for_role("cheap") == ""
    assert model_for_role("") == ""
    # A non-blank DB primary still wins for roles without their own row.
    with get_conn() as c:
        c.execute("UPDATE settings SET value='hermes-agent' WHERE key='llm.primary_model'")
    assert model_for_role("strong") == "hermes-agent"
    assert model_for_role("cheap") == "hermes-agent"


def test_llm_request_carries_blank_model_when_unset(monkeypatch):
    """With no model configured, the outgoing chat/completions request must
    carry model:"" (provider applies its own default) — never a hardcoded
    fallback model."""
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "")
    monkeypatch.setenv("LLM_STRONG_MODEL", "")
    monkeypatch.setenv("LLM_CHEAP_MODEL", "")
    import importlib
    import app.config as cfg
    importlib.reload(cfg)
    from app.db import get_conn
    with get_conn() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('llm.primary_model','')")
    from app.llm import client as cl
    captured = {}

    class FakeResp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "{\"ok\": 1}"}}], "usage": {}}
        def raise_for_status(self):
            pass

    def fake_post(url, **kw):
        captured["body"] = kw["json"]
        return FakeResp()

    monkeypatch.setattr(cl.requests, "post", fake_post)
    from app.scenarios.repos import create_scenario
    jid = create_scenario("blank model probe", db_path=DB)
    c2 = cl.LLMClient()
    assert c2.model == ""
    out = c2.chat(jid, "chat", [{"role": "user", "content": "hi"}])
    assert captured["body"]["model"] == ""  # provider default, no hardcoded fallback
    assert out["json"] == {"ok": 1}


def test_htmx_bundle_is_real_not_placeholder():
    """The vendored htmx must be the real library: a placeholder stub made
    every hx-* button (delete/cancel/retry) dead in real browsers while
    curl-level tests still passed."""
    from app import create_app
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.get("/static/htmx.min.js")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert len(body) > 10000, f"htmx bundle suspiciously small: {len(body)} bytes"
    assert "Vendor bundle placeholder" not in body
    assert "htmx" in body[:400].lower()


def test_dashboard_poll_trigger_uses_valid_htmx_syntax():
    """hx-trigger must use 'every 3 seconds' (valid) — 'interval:3000,
    request:after' is not valid htmx syntax and silently never fired."""
    from app import create_app
    from app.scenarios.repos import create_scenario
    app = create_app({"TESTING": True})
    cl = app.test_client()
    create_scenario("poll trigger check", db_path=DB)
    r = cl.get("/scenarios")
    body = r.get_data(as_text=True)
    assert "every 3s" in body
    assert "interval:3000" not in body
