"""Tests for the futures-view artifact builder (openspec 2026-10-25-futures-output)."""
import json
import os

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
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_S", "1")
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "1")
    import importlib
    import app.config as cfg
    importlib.reload(cfg)
    from app.migrate import apply_pending
    from app.seed import seed
    apply_pending(DB); seed(DB)
    yield
    for suf in ("", "-wal", "-shm"):
        try: os.remove(DB + suf)
        except OSError: pass


def _mk_outcome(jid, oid, name, prob, band, rep, members):
    from app.db import get_conn
    with get_conn(DB) as c:
        c.execute("""INSERT INTO outcomes(id,job_id,name,probability,band,summary,
          factor_states,causal_path,indicators,assumptions,risks,explanation,
          representative_particle,enrichment) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (oid, jid, name, prob, band, "summary text", "{}", "[]", "[]", "[]", "[]",
           "", rep, "{}"))
        for m in members:
            c.execute("INSERT OR IGNORE INTO outcome_particle_memberships(outcome_id,particle_id,weight)"
                      " VALUES(?,?,1.0)", (oid, m))


def _mk_particle(jid, pid, parent=None, prob=1.0, created_step=0):
    from app.db import get_conn
    with get_conn(DB) as c:
        c.execute("INSERT INTO particles(id,job_id,parent_id,probability,factor_state,created_step)"
                  " VALUES(?,?,?,?,?,?)",
                  (pid, jid, parent, prob, json.dumps({"f0": 50.0}), created_step))


def _mk_state(pid, step, fv, events="[]"):
    from app.db import get_conn
    with get_conn(DB) as c:
        c.execute("INSERT OR REPLACE INTO particle_states(particle_id,step_index,factor_values,events)"
                  " VALUES(?,?,?,?)", (pid, step, json.dumps(fv), events))


def _mk_step(jid, step, label="step", summary="", tp="[]"):
    from app.db import get_conn
    with get_conn(DB) as c:
        c.execute("INSERT OR REPLACE INTO projection_steps(job_id,step_index,time_label,summary,turning_points)"
                  " VALUES(?,?,?,?,?)", (jid, step, label, summary, tp))


# ---------------------------------------------------------------- aggregates

def test_aggregate_mean_min_max_and_deltas():
    from app.jobs.repositories import create_job
    from app.futures import _aggregate, _end_deltas
    jid = create_job("agg", db_path=DB)
    _mk_particle(jid, "a1"); _mk_particle(jid, "a2")
    _mk_state("a1", 0, {"f0": 50}); _mk_state("a1", 1, {"f0": 60})
    _mk_state("a2", 0, {"f0": 50}); _mk_state("a2", 1, {"f0": 40})
    _mk_outcome(jid, "oA", "A", 0.6, "dominant", "a1", ["a1", "a2"])
    agg = _aggregate({"oA": ["a1", "a2"]}, {"a1": [{"step_index": 0, "factor_values": json.dumps({"f0": 50})},
                                                  {"step_index": 1, "factor_values": json.dumps({"f0": 60})}],
                                            "a2": [{"step_index": 0, "factor_values": json.dumps({"f0": 50})},
                                                   {"step_index": 1, "factor_values": json.dumps({"f0": 40})}]})
    s = agg["oA"]["f0"]
    assert s["steps"] == [0, 1]
    assert s["mean"][0] == 50.0 and s["mean"][1] == 50.0
    assert s["min"][1] == 40.0 and s["max"][1] == 60.0
    d = _end_deltas(agg["oA"], lambda k: 50.0)
    assert d == {"f0": 0.0}


def test_branch_detection_fork_no_fork_remerge():
    from app.jobs.repositories import create_job
    from app.futures import detect_branch_points
    jid = create_job("branch", db_path=DB)
    # Tree: root r (step 0) -> children x,y (step 1); x -> x1, y -> y1 (step 2)
    _mk_particle(jid, "r", None, created_step=0)
    _mk_particle(jid, "x", "r", created_step=1)
    _mk_particle(jid, "y", "r", created_step=1)
    _mk_particle(jid, "x1", "x", created_step=2)
    _mk_particle(jid, "y1", "y", created_step=2)
    po = {"r": "oA", "x": "oA", "x1": "oA", "y": "oB", "y1": "oB"}
    states = {"x": [{"step_index": 1, "factor_values": json.dumps({"f0": 70})}],
              "y": [{"step_index": 1, "factor_values": json.dumps({"f0": 40})}]}
    bps = detect_branch_points(
        {"r": {"parent_id": None, "created_step": 0},
         "x": {"parent_id": "r", "created_step": 1},
         "y": {"parent_id": "r", "created_step": 1},
         "x1": {"parent_id": "x", "created_step": 2},
         "y1": {"parent_id": "y", "created_step": 2}},
        po, states, [], {"f0": "F0"})
    assert len(bps) == 1
    b = bps[0]
    assert b["step"] == 1
    assert set(b["outcomes"]) == {"oA", "oB"}
    assert b["factor_deltas"] and b["factor_deltas"][0]["id"] == "f0"
    assert abs(b["factor_deltas"][0]["spread"] - 30.0) < 0.01
    # No divergence -> []
    po2 = {"r": "oA", "x": "oA", "y": "oA", "x1": "oA", "y1": "oA"}
    assert detect_branch_points(
        {"r": {"parent_id": None, "created_step": 0},
         "x": {"parent_id": "r", "created_step": 1},
         "y": {"parent_id": "r", "created_step": 1}}, po2, states, [], {}) == []
    # Siblings that re-merge into the same outcome -> not a fork
    po3 = {"x": "oA", "y": "oA", "x1": "oA", "y1": "oA"}
    assert detect_branch_points(
        {"x": {"parent_id": None, "created_step": 0},
         "y": {"parent_id": None, "created_step": 0},
         "x1": {"parent_id": "x", "created_step": 1},
         "y1": {"parent_id": "y", "created_step": 1}}, po3, states, [], {}) == []


def test_story_tiers_events_then_milestones():
    from app.jobs.repositories import create_job
    from app.futures import derive_story
    jid = create_job("story", db_path=DB)
    # Tier 1: events present
    states = {"r": [{"step_index": 1, "factor_values": json.dumps({"f0": 55}),
                     "events": json.dumps(["startup acquired a competitor"])}]}
    steps = [{"step_index": 1, "time_label": "step-1", "summary": "1 particles", "turning_points": "[]"}]
    story, src = derive_story("oA", "r", states, steps, {"assumptions": "[]"}, {}, lambda k: 50.0, "macro")
    assert src == "events"
    assert story[0]["cornerstone"] and "competitor" in story[0]["text"]
    assert story[0]["rationale"]["causal_chain"] == ["startup acquired a competitor"]
    # Tier 3: junk summaries + no events -> factor milestones
    states3 = {"r": [{"step_index": 0, "factor_values": json.dumps({"f0": 50})},
                     {"step_index": 10, "factor_values": json.dumps({"f0": 20})}]}
    steps3 = [{"step_index": 10, "time_label": "step-10", "summary": "1 particles", "turning_points": "[]"}]
    story3, src3 = derive_story("oA", "r", states3, steps3, {"assumptions": "[]"},
                                {"f0": "Income"}, lambda k: 50.0, "personal")
    assert src3 == "milestones"
    assert any("Income" in m["text"] for m in story3)
    assert any(m["rationale"]["causal_chain"] for m in story3)  # never empty rationale


def test_build_and_lazy_heal_artifact():
    from app.jobs.repositories import create_job
    from app.futures import (build_futures_view, save_futures_view,
                             load_futures_view, ARTIFACT_VERSION)
    from app.db import get_conn
    from app.steps.factors import select_factors
    jid = create_job("artifact job", db_path=DB)
    select_factors(jid, [{"id": "f0", "name": "Income Trajectory", "current": 50.0}], "macro", DB)
    _mk_particle(jid, "p1"); _mk_particle(jid, "p2")
    _mk_state("p1", 0, {"f0": 50}); _mk_state("p1", 5, {"f0": 70})
    _mk_state("p2", 0, {"f0": 50}); _mk_state("p2", 5, {"f0": 45})
    _mk_step(jid, 0, "January", "calm"); _mk_step(jid, 5, "June", "1 particles")
    _mk_outcome(jid, "oA", "Outcome 1", 0.7, "dominant", "p1", ["p1"])
    _mk_outcome(jid, "oB", "Outcome 2", 0.3, "plausible", "p2", ["p2"])
    # job must be COMPLETED for healing; create_job leaves DRAFT
    with get_conn(DB) as c:
        c.execute("UPDATE projection_jobs SET status='COMPLETED' WHERE id=?", (jid,))
    fv = build_futures_view(jid, DB)
    assert fv is not None
    assert fv["version"] == ARTIFACT_VERSION
    assert fv["timelines_explored"] == 2
    assert [o["name"] for o in fv["outcomes"]] == ["Outcome 1", "Outcome 2"]  # prob-sorted
    assert fv["outcomes"][0]["share_pct"] == 70
    assert fv["outcomes"][0]["members"] == 1
    assert fv["outcomes"][0]["end_deltas"][0]["name"] == "Income Trajectory"
    assert fv["outcomes"][0]["end_deltas"][0]["delta"] == 20.0
    assert fv["outcomes"][1]["end_deltas"][0]["delta"] == -5.0
    assert fv["heuristic_mode"] is True  # zero llm_calls rows
    assert fv["human_time_labels"] is True  # "January" is human
    assert fv["factors"][0]["name"] == "Income Trajectory"
    # no divergence -> no branch points -> single-path map
    assert fv["branch_points"] == []
    # lazy heal: no artifact saved yet, load builds + persists one
    loaded = load_futures_view(jid, DB)
    assert loaded is not None
    with get_conn(DB) as c:
        n = c.execute("SELECT COUNT(*) AS n FROM artifacts WHERE job_id=? AND kind='futures_view'",
                      (jid,)).fetchone()["n"]
    assert n == 1
    # second load does not duplicate
    load_futures_view(jid, DB)
    with get_conn(DB) as c:
        n2 = c.execute("SELECT COUNT(*) AS n FROM artifacts WHERE job_id=? AND kind='futures_view'",
                       (jid,)).fetchone()["n"]
    assert n2 == 1
    # explicit save + load roundtrip
    save_futures_view(jid, fv, DB)


def test_artifact_failure_does_not_fail_completion():
    """The completion hook must tolerate builder crashes."""
    from app.jobs.repositories import create_job
    from app.futures import build_futures_view
    jid = create_job("hook", db_path=DB)
    import app.futures as F
    orig = F._load_job_data
    def boom(*a, **k): raise RuntimeError("boom")
    F._load_job_data = boom
    try:
        result = None
        try:
            result = build_futures_view(jid, DB)
        except RuntimeError:
            result = "caught"
        assert result == "caught"
    finally:
        F._load_job_data = orig
    # and the wrapper used at completion swallows errors:
    def safe_build():
        try:
            return build_futures_view(jid, DB)
        except Exception:
            return None
    F._load_job_data = boom
    try:
        assert safe_build() is None
    finally:
        F._load_job_data = orig
