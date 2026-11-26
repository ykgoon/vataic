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


def _seed_completed_scenario(scope="personal"):
    from app.db import get_conn
    from app.scenarios.repos import create_scenario
    from app.steps.factors import select_factors
    jid = create_scenario("I am considering quitting my scenario to go independent.",
                     db_path=DB, scope=scope)
    select_factors(jid, [{"id": "f0", "name": "Income Trajectory", "current": 50.0},
                         {"id": "f1", "name": "Health Energy", "current": 50.0}],
                   scope, DB)
    with get_conn(DB) as c:
        c.execute("INSERT OR REPLACE INTO particles(id,scenario_id,parent_id,probability,factor_state,created_step)"
                  " VALUES('pa',?,NULL,0.7,?,0)", (jid, json.dumps({"f0": 78.0, "f1": 34.0})))
        c.execute("INSERT OR REPLACE INTO particles(id,scenario_id,parent_id,probability,factor_state,created_step)"
                  " VALUES('pb',?,NULL,0.3,?,0)", (jid, json.dumps({"f0": 36.0, "f1": 55.0})))
        for pid, s in (("pa", [{"step_index": 0, "factor_values": {"f0": 50, "f1": 50}},
                               {"step_index": 12, "factor_values": {"f0": 78, "f1": 34}}]),
                       ("pb", [{"step_index": 0, "factor_values": {"f0": 50, "f1": 50}},
                               {"step_index": 12, "factor_values": {"f0": 36, "f1": 55}}])):
            for st in s:
                c.execute("INSERT OR REPLACE INTO particle_states(particle_id,step_index,factor_values,events)"
                          " VALUES(?,?,?,'[]')", (pid, st["step_index"], json.dumps(st["factor_values"])))
        c.execute("INSERT OR REPLACE INTO projection_steps(scenario_id,step_index,time_label,summary,turning_points)"
                  " VALUES(?,0,'now','2 particles','[]')", (jid,))
        c.execute("INSERT OR REPLACE INTO projection_steps(scenario_id,step_index,time_label,summary,turning_points)"
                  " VALUES(?,12,'month 12','1 particles','[]')", (jid,))
        c.execute("INSERT INTO outcomes(id,scenario_id,name,probability,band,summary,factor_states,"
                  "causal_path,indicators,assumptions,risks,explanation,representative_particle,enrichment)"
                  " VALUES('oA',?, 'Grind through',0.7,'dominant','Income rises while health pays for it.',"
                  "'{}','[]','[\"Income crosses its baseline by month 6\"]','[]','[]','','pa','{}')", (jid,))
        c.execute("INSERT INTO outcomes(id,scenario_id,name,probability,band,summary,factor_states,"
                  "causal_path,indicators,assumptions,risks,explanation,representative_particle,enrichment)"
                  " VALUES('oB',?, 'Stall out',0.3,'plausible','Income drifts below baseline.',"
                  "'{}','[]','[]','[]','[]','','pb','{}')", (jid,))
        c.execute("INSERT OR IGNORE INTO outcome_particle_memberships(outcome_id,particle_id,weight)"
                  " VALUES('oA','pa',0.7)")
        c.execute("INSERT OR IGNORE INTO outcome_particle_memberships(outcome_id,particle_id,weight)"
                  " VALUES('oB','pb',0.3)")
        c.execute("UPDATE projection_scenarios SET status='COMPLETED' WHERE id=?", (jid,))
    return jid


def test_futures_view_renders_all_sections():
    from app import create_app
    jid = _seed_completed_scenario("personal")
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.get(f"/scenarios/{jid}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # futures sections now render inside the scenario page
    assert "2 timelines explored" in body
    assert "heuristic mode" in body          # zero llm_calls
    assert "horizon:" in body
    # probability split with share-and-count
    assert "70%" in body and "30%" in body
    assert "1 of 2 timelines" in body
    # fan chart present, with the start level named in plain words (the
    # storage word "baseline" is deliberately never user-facing)
    assert "fan-svg" in body and "where it started" in body
    # personal-scope phrasing
    assert "takes your factors" in body
    # futures map + story panel
    assert "fmap-svg" in body
    assert "story-panel" in body
    # the premise appears once, in the scenario header
    assert body.count("quitting my scenario to go independent") == 1
    # outcome cards with collapsed details
    assert "Grind through" in body and "Stall out" in body
    assert "<details" in body
    # retired debug pages are not linked from the results view
    assert "/tree" not in body
    assert "/trajectories" not in body


def test_outcome_cards_consolidate_redundant_sections():
    """Each card must carry one narrative section, not four overlapping lists.

    Causal path / Watch for / Assumes / What could break it all restated the
    same ranked factors, so they are folded into a single story section with
    one watch-and-unwind line per factor.
    """
    from app import create_app
    jid = _seed_completed_scenario("personal")
    app = create_app({"TESTING": True})
    cl = app.test_client()
    body = cl.get(f"/scenarios/{jid}").get_data(as_text=True)
    for retired in (">Watch for<", ">Assumes<", ">What could break it<",
                    ">Causal path<"):
        assert retired not in body, retired
    assert body.count(">Read the full story<") >= 1

    oid_body = cl.get(f"/results/{jid}/story?oid=oA&expand=1&hide_explanation=1")
    assert oid_body.status_code == 200
    story = oid_body.get_data(as_text=True)
    assert 'class="story-explanation"' not in story
    # Plain-language watch/unwind line present when a factor moves.
    assert "What to watch, and what would undo this path" in story
    assert "this path unwinds" in story


def test_retired_debug_pages_are_gone():
    """The timeline-tree and factor-trajectories pages were removed: no links
    anywhere in the results views, and the routes no longer resolve."""
    from app import create_app
    jid = _seed_completed_scenario("personal")
    app = create_app({"TESTING": True})
    cl = app.test_client()

    for path in (f"/results/{jid}/fan?factor=f1",
                 f"/results/{jid}/story?oid=oA", f"/scenarios/{jid}"):
        body = cl.get(path).get_data(as_text=True)
        assert f"/results/{jid}/tree" not in body, path
        assert f"/results/{jid}/trajectories" not in body, path

    for path in (f"/results/{jid}/tree", f"/results/{jid}/trajectories",
                 f"/results/{jid}/branch/pa"):
        assert cl.get(path).status_code == 404, path

    # The fan chart still serves the factor timeline it replaced.
    assert "fan-svg" in cl.get(f"/results/{jid}/fan?factor=f1").get_data(as_text=True)


def test_macro_scope_phrasing():
    from app import create_app
    jid = _seed_completed_scenario("macro")
    app = create_app({"TESTING": True})
    cl = app.test_client()
    body = cl.get(f"/scenarios/{jid}").get_data(as_text=True)
    assert "takes the key factors" in body
    assert "takes your factors" not in body


def test_fan_and_story_partials():
    from app import create_app
    jid = _seed_completed_scenario("personal")
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
    assert "What to watch, and what would undo this path" in body2


def test_rationale_missing_branch_is_graceful():
    from app import create_app
    jid = _seed_completed_scenario("personal")
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.get(f"/results/{jid}/rationale?bp=5")
    assert r.status_code == 200
    assert "no fork recorded" in r.get_data(as_text=True)


def test_incomplete_job_shows_stored_outcomes_not_futures():
    from app import create_app
    from app.db import get_conn
    jid = _seed_completed_scenario("personal")
    with get_conn(DB) as c:
        c.execute("UPDATE projection_scenarios SET status='RUNNING' WHERE id=?", (jid,))
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.get(f"/scenarios/{jid}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # stored-outcome grid, not the futures sections
    assert "outcome-card" in body
    assert "fmap-svg" not in body


def test_new_futures_view_renders_all_sections():
    """Cross-check with a scenario completed through the real synthesis path."""
    from app import create_app
    from app.db import get_conn
    from app.scenarios.repos import create_scenario
    from app.steps.factors import select_factors
    from app.steps.synthesis import synthesize
    jid = create_scenario("channel premise", db_path=DB)
    select_factors(jid, [{"id": "f0", "name": "Reputation", "current": 50.0}], "macro", DB)
    parts = [{"id": "p0", "factor_state": {"f0": 80.0}, "probability": 0.75},
             {"id": "p1", "factor_state": {"f0": 30.0}, "probability": 0.25}]
    synthesize(jid, parts, 2, db_path=DB, premise="channel premise")
    with get_conn(DB) as c:
        c.execute("UPDATE projection_scenarios SET status='COMPLETED' WHERE id=?", (jid,))
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.get(f"/scenarios/{jid}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "2 timelines explored" in body
    assert "fmap-svg" in body
    # outcome names present in split, map labels, and cards
    assert "Outcome 1" in body


def test_results_url_redirects_to_the_scenario_page():
    """Old bookmarks and external links must not break when the two pages merge."""
    from app import create_app
    jid = _seed_completed_scenario("personal")
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.get(f"/results/{jid}")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"/scenarios/{jid}")

    # Following it lands on the page that now carries the result.
    body = cl.get(f"/scenarios/{jid}").get_data(as_text=True)
    assert "2 timelines explored" in body
    assert "fmap-svg" in body


def test_scenario_page_carries_both_halves():
    """One page, both halves: the job's telemetry and the result's reading.

    The merge only succeeds if nothing was dropped: a completed scenario must
    show its progress/loop-bounds telemetry *and* the futures sections, and must
    not repeat the outcome list in two different card styles.
    """
    from app import create_app
    jid = _seed_completed_scenario("personal")
    app = create_app({"TESTING": True})
    cl = app.test_client()
    body = cl.get(f"/scenarios/{jid}").get_data(as_text=True)

    # Result half
    for marker in ("How the futures split", "fan-svg", "fmap-svg",
                   "story-panel", "cards-row"):
        assert marker in body, marker
    # Telemetry half
    for marker in ("Progress", "Loop Bounds", "Elapsed:", "status-badge"):
        assert marker in body, marker
    # Premise shown once
    assert body.count("quitting my scenario to go independent") == 1
    # No duplicated outcome listing: the detail page's own outcome grid is
    # suppressed when the futures cards already cover every field it showed.
    assert 'class="outcomes-grid"' not in body
    # No button to a page that is now the same page.
    assert "View Results" not in body


def test_failed_scenario_shows_error_and_no_futures():
    """A failed run must still show why it failed, on the same single page."""
    from app import create_app
    from app.db import get_conn
    jid = _seed_completed_scenario("personal")
    with get_conn(DB) as c:
        c.execute("UPDATE projection_scenarios SET status='FAILED', error=? WHERE id=?",
                  ("provider refused the request", jid))
    app = create_app({"TESTING": True})
    cl = app.test_client()
    body = cl.get(f"/scenarios/{jid}").get_data(as_text=True)
    assert "provider refused the request" in body
    assert "scenario-error" in body
    # no futures sections for a run that never produced one
    assert "fmap-svg" not in body
    assert "How the futures split" not in body


def test_decision_context_survives_the_merge():
    """decision_context was on both old pages; it must appear exactly once."""
    from app import create_app
    from app.db import get_conn
    jid = _seed_completed_scenario("personal")
    with get_conn(DB) as c:
        c.execute("UPDATE projection_scenarios SET decision_context=? WHERE id=?",
                  ("Choosing whether to leave a stable role.", jid))
    app = create_app({"TESTING": True})
    cl = app.test_client()
    body = cl.get(f"/scenarios/{jid}").get_data(as_text=True)
    assert body.count("Choosing whether to leave a stable role.") == 1
    assert "scenario-decision-context" in body


def test_engine_summary_reaches_the_reader():
    """The stored summary is shown nowhere else, so it must survive in the
    collapsed path story for every outcome."""
    from app import create_app
    jid = _seed_completed_scenario("personal")
    app = create_app({"TESTING": True})
    cl = app.test_client()
    for oid in ("oA", "oB"):
        body = cl.get(f"/results/{jid}/story?oid={oid}&expand=1").get_data(as_text=True)
        assert "story-summary" in body, oid
