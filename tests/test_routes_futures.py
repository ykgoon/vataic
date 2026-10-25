"""Route tests for the futures view (openspec 2026-10-25-futures-output)."""
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


def _seed_completed_job(scope="personal"):
    from app.db import get_conn
    from app.jobs.repositories import create_job
    from app.steps.factors import select_factors
    jid = create_job("I am considering quitting my job to go independent.",
                     db_path=DB, scope=scope)
    select_factors(jid, [{"id": "f0", "name": "Income Trajectory", "current": 50.0},
                         {"id": "f1", "name": "Health Energy", "current": 50.0}],
                   scope, DB)
    with get_conn(DB) as c:
        c.execute("INSERT OR REPLACE INTO particles(id,job_id,parent_id,probability,factor_state,created_step)"
                  " VALUES('pa',?,NULL,0.7,?,0)", (jid, json.dumps({"f0": 78.0, "f1": 34.0})))
        c.execute("INSERT OR REPLACE INTO particles(id,job_id,parent_id,probability,factor_state,created_step)"
                  " VALUES('pb',?,NULL,0.3,?,0)", (jid, json.dumps({"f0": 36.0, "f1": 55.0})))
        for pid, s in (("pa", [{"step_index": 0, "factor_values": {"f0": 50, "f1": 50}},
                               {"step_index": 12, "factor_values": {"f0": 78, "f1": 34}}]),
                       ("pb", [{"step_index": 0, "factor_values": {"f0": 50, "f1": 50}},
                               {"step_index": 12, "factor_values": {"f0": 36, "f1": 55}}])):
            for st in s:
                c.execute("INSERT OR REPLACE INTO particle_states(particle_id,step_index,factor_values,events)"
                          " VALUES(?,?,?,'[]')", (pid, st["step_index"], json.dumps(st["factor_values"])))
        c.execute("INSERT OR REPLACE INTO projection_steps(job_id,step_index,time_label,summary,turning_points)"
                  " VALUES(?,0,'now','2 particles','[]')", (jid,))
        c.execute("INSERT OR REPLACE INTO projection_steps(job_id,step_index,time_label,summary,turning_points)"
                  " VALUES(?,12,'month 12','1 particles','[]')", (jid,))
        c.execute("INSERT INTO outcomes(id,job_id,name,probability,band,summary,factor_states,"
                  "causal_path,indicators,assumptions,risks,explanation,representative_particle,enrichment)"
                  " VALUES('oA',?, 'Grind through',0.7,'dominant','Income rises while health pays for it.',"
                  "'{}','[]','[\"Income crosses its baseline by month 6\"]','[]','[]','','pa','{}')", (jid,))
        c.execute("INSERT INTO outcomes(id,job_id,name,probability,band,summary,factor_states,"
                  "causal_path,indicators,assumptions,risks,explanation,representative_particle,enrichment)"
                  " VALUES('oB',?, 'Stall out',0.3,'plausible','Income drifts below baseline.',"
                  "'{}','[]','[]','[]','[]','','pb','{}')", (jid,))
        c.execute("INSERT OR IGNORE INTO outcome_particle_memberships(outcome_id,particle_id,weight)"
                  " VALUES('oA','pa',0.7)")
        c.execute("INSERT OR IGNORE INTO outcome_particle_memberships(outcome_id,particle_id,weight)"
                  " VALUES('oB','pb',0.3)")
        c.execute("UPDATE projection_jobs SET status='COMPLETED' WHERE id=?", (jid,))
    return jid


def test_futures_view_renders_all_sections():
    from app import create_app
    jid = _seed_completed_job("personal")
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.get(f"/results/{jid}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # overview strip
    assert "Futures overview" in body
    assert "2 timelines explored" in body
    assert "heuristic mode" in body          # zero llm_calls
    assert "horizon:" in body
    # probability split with share-and-count
    assert "70%" in body and "30%" in body
    assert "1 of 2 timelines" in body
    # fan chart present with baseline
    assert "fan-svg" in body and "baseline" in body
    # personal-scope phrasing
    assert "takes your factors" in body
    # futures map + story panel
    assert "fmap-svg" in body
    assert "story-panel" in body
    # outcome cards with collapsed details
    assert "Grind through" in body and "Stall out" in body
    assert "<details" in body
    # tree demoted
    assert "Timeline Tree (debug)" in body


def test_macro_scope_phrasing():
    from app import create_app
    jid = _seed_completed_job("macro")
    app = create_app({"TESTING": True})
    cl = app.test_client()
    body = cl.get(f"/results/{jid}").get_data(as_text=True)
    assert "takes the key factors" in body
    assert "takes your factors" not in body


def test_fan_and_story_partials():
    from app import create_app
    jid = _seed_completed_job("personal")
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.get(f"/results/{jid}/fan?factor=f1")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "fan-svg" in body
    assert "Health Energy" in body
    r2 = cl.get(f"/results/{jid}/story?oid=oA")
    assert r2.status_code == 200
    body2 = r2.get_data(as_text=True)
    assert "Grind through" in body2
    assert "story-source-note" in body2       # milestones disclosure
    assert "Early signals" in body2 or "early-signals" in body2


def test_rationale_missing_branch_is_graceful():
    from app import create_app
    jid = _seed_completed_job("personal")
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.get(f"/results/{jid}/rationale?bp=5")
    assert r.status_code == 200
    assert "no fork recorded" in r.get_data(as_text=True)


def test_legacy_fallback_for_incomplete_job():
    from app import create_app
    from app.db import get_conn
    jid = _seed_completed_job("personal")
    with get_conn(DB) as c:
        c.execute("UPDATE projection_jobs SET status='RUNNING' WHERE id=?", (jid,))
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.get(f"/results/{jid}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # legacy card page, not the futures view
    assert "outcome-card" in body
    assert "fmap-svg" not in body


def test_new_futures_view_renders_all_sections():
    """Cross-check with a job completed through the real synthesis path."""
    from app import create_app
    from app.db import get_conn
    from app.jobs.repositories import create_job
    from app.steps.factors import select_factors
    from app.steps.synthesis import synthesize
    jid = create_job("channel premise", db_path=DB)
    select_factors(jid, [{"id": "f0", "name": "Reputation", "current": 50.0}], "macro", DB)
    parts = [{"id": "p0", "factor_state": {"f0": 80.0}, "probability": 0.75},
             {"id": "p1", "factor_state": {"f0": 30.0}, "probability": 0.25}]
    synthesize(jid, parts, 2, db_path=DB, premise="channel premise")
    with get_conn(DB) as c:
        c.execute("UPDATE projection_jobs SET status='COMPLETED' WHERE id=?", (jid,))
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.get(f"/results/{jid}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Futures overview" in body
    assert "2 timelines explored" in body
    assert "fmap-svg" in body
    # outcome names present in split, map labels, and cards
    assert "Outcome 1" in body
