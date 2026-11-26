"""Tests: causal propagation and measured order-of-effect.

The engine used to compute a causal graph and never use it, so every factor
moved only when a hash named it and nothing was ever second-order. These tests
pin the replacement: deltas travel along the graph, and an outcome's order is
measured from where its movement came from.
"""
import json
import os

import pytest

DB = "/tmp/vataic-causality-test.db"


@pytest.fixture(autouse=True)
def _db(monkeypatch):
    """Own database per test. The fixture in test_scenarios.py is module-scoped
    to that file, so these tests must set up their own: without it create_scenario
    writes to the real data directory while the assertions read the test DB."""
    for suf in ("", "-wal", "-shm"):
        try:
            os.remove(DB + suf)
        except OSError:
            pass
    monkeypatch.setenv("DATABASE_PATH", DB)
    monkeypatch.setenv("ARTIFACT_DIR", "/tmp/vataic-causality-artifacts")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_S", "1")
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("VATAIC_ALLOW_HEURISTIC", "1")
    import importlib
    import app.config as cfg
    importlib.reload(cfg)
    import app.llm.client as cl
    cl.reset_breaker()
    from app.migrate import apply_pending
    from app.seed import seed
    apply_pending(DB)
    seed(DB)
    yield


def _scenario(premise="quitting my scenario to go independent"):
    from app.scenarios.repos import create_scenario
    return create_scenario(premise, db_path=DB)


def _state(jid, pid, hops, step=1):
    from app.db import get_conn
    with get_conn(DB) as c:
        c.execute("INSERT OR IGNORE INTO particles(id,scenario_id,probability,"
                  "factor_state,created_step) VALUES(?,?,1.0,'{}',1)", (pid, jid))
        c.execute("INSERT OR REPLACE INTO particle_states(particle_id,step_index,"
                  "factor_values,events,causal_hops,causal_source) "
                  "VALUES(?,?,'{}','[]',?,'graph')", (pid, step, json.dumps(hops)))


# ---- propagation mechanics (no DB) ----

EDGES = [
    {"source": "a", "target": "b", "strength": 0.5, "delay_days": 10},
    {"source": "b", "target": "c", "strength": 0.4, "delay_days": 5},
    {"source": "a", "target": "d", "strength": -0.6, "delay_days": 3},
]


def test_cascade_reaches_three_hops():
    from app.steps.causality import propagate
    res = {r["factor"]: r for r in propagate({"a": 20.0}, EDGES)}
    assert res["b"]["hop"] == 1
    assert res["d"]["hop"] == 1
    assert res["c"]["hop"] == 2
    # strength compounds along the path: 20 * 0.5 * 0.4
    assert res["c"]["delta"] == pytest.approx(4.0)
    assert res["b"]["delta"] == pytest.approx(10.0)


def test_inverse_edge_pushes_the_other_way():
    from app.steps.causality import propagate
    res = {r["factor"]: r for r in propagate({"a": 20.0}, EDGES)}
    assert res["d"]["delta"] < 0     # a -> d has strength -0.6
    assert res["b"]["delta"] > 0


def test_delay_accumulates_along_the_path():
    from app.steps.causality import propagate
    res = {r["factor"]: r for r in propagate({"a": 20.0}, EDGES)}
    assert res["b"]["delay_days"] == 10
    assert res["c"]["delay_days"] == 15


def test_hop_depths_reports_root_upstream():
    from app.steps.causality import hop_depths
    assert hop_depths(EDGES) == {"a": 0, "b": 1, "d": 1, "c": 2}


def test_no_graph_means_no_cascade():
    from app.steps.causality import apply_cascade
    state, hops, induced = apply_cascade({"a": 50}, {"a": 10.0}, [])
    assert induced == []
    assert hops == {"a": 0}
    assert state["a"] == 60.0


def test_cascade_clamps_to_the_scale():
    from app.steps.causality import apply_cascade
    state, _, _ = apply_cascade({"a": 95, "b": 95}, {"a": 50.0}, EDGES)
    assert all(0.0 <= v <= 100.0 for v in state.values())


def test_split_seed_drops_unknown_and_negligible():
    from app.steps.causality import split_seed
    assert split_seed({"a", "b"}, {"a": 5.0, "zzz": 9.0, "b": 0.01}) == {"a": 5.0}


def test_hop_mass_separates_direct_from_induced():
    from app.steps.causality import apply_cascade, hop_mass, split_seed
    seed = split_seed({"a", "b", "c", "d"}, {"a": 20.0})
    _state, _hops, induced = apply_cascade(
        {"a": 50, "b": 50, "c": 50, "d": 50}, seed, EDGES)
    mass = hop_mass(seed, induced)
    assert mass["0"] == pytest.approx(20.0)
    assert mass["1"] == pytest.approx(22.0)   # b (10) + d (12)
    assert mass["2"] == pytest.approx(4.0)    # c


def test_apply_cascade_still_reports_direct_hops():
    from app.steps.causality import apply_cascade
    _s, hops, _i = apply_cascade({"a": 50, "b": 50, "c": 50, "d": 50},
                                 {"a": 20.0}, EDGES)
    assert hops["a"] == 0
    assert hops["b"] == 1
    assert hops["c"] == 2


# ---- the graph actually reaches the loop ----

def test_default_graph_is_built_when_the_llm_is_unavailable():
    """`llm_causal_edges() or []` passed an empty list, which is not None, so
    build_causal_graph never took its default-edges branch and every scenario
    stored a graph with zero edges."""
    from app.steps import factors as F
    fl = [{"id": f"f{i}", "name": f"F{i}"} for i in range(5)]
    jid = _scenario()
    # None (not []) is what the caller must pass through
    g = F.build_causal_graph(jid, fl, None, db_path=DB)
    assert g["edges"], "fallback must produce edges"
    # an explicit empty list means "the model said there are none"
    jid2 = _scenario()
    g2 = F.build_causal_graph(jid2, fl, [], db_path=DB)
    assert g2["edges"] == []


def test_stored_edges_load_back():
    from app.db import get_conn
    from app.steps.causality import load_edges
    jid = _scenario()
    edges = [{"source": "f0", "target": "f1", "strength": 0.5, "delay_days": 3}]
    with get_conn(DB) as c:
        c.execute("INSERT INTO causal_graphs(scenario_id,nodes,edges) VALUES(?,?,?)",
                  (jid, json.dumps(["f0", "f1"]), json.dumps(edges)))
    assert load_edges(jid, db_path=DB) == edges


def test_load_edges_is_empty_without_a_graph():
    from app.steps.causality import load_edges
    assert load_edges("no-such-scenario-xyz") == []


# ---- order is measured, not assumed ----

def test_order_is_measured_from_hop_mass():
    from app.steps.synthesis import _measured_order
    jid = _scenario()
    _state(jid, "pA", {"hops": {"oA": 0, "oB": 1}, "mass": {"0": 30.0, "1": 5.0}})
    m = _measured_order(jid, ["pA"], {"oA": 80, "oB": 55}, db_path=DB)
    assert m["source"] == "measured"
    assert m["order"] == 1, m


def test_order_is_second_when_movement_is_mostly_downstream():
    from app.steps.synthesis import _measured_order
    jid = _scenario()
    _state(jid, "pB", {"hops": {"oA": 0, "oB": 1}, "mass": {"0": 2.0, "1": 40.0}})
    m = _measured_order(jid, ["pB"], {"oA": 52, "oB": 80}, db_path=DB)
    assert m["order"] == 2, m


def test_order_is_third_when_the_cascade_dominates():
    from app.steps.synthesis import _measured_order
    jid = _scenario()
    _state(jid, "pD", {"hops": {"oA": 0, "oB": 3}, "mass": {"0": 1.0, "3": 40.0}})
    m = _measured_order(jid, ["pD"], {"oA": 51, "oB": 90}, db_path=DB)
    assert m["order"] == 3, m


def test_unmeasured_outcomes_claim_no_order():
    """Rows written before the causal loop have no hop record; a default 1 must
    not be presented as if it had been measured."""
    from app.steps.synthesis import _measured_order
    jid = _scenario()
    m = _measured_order(jid, ["nobody"], {"oA": 80}, db_path=DB)
    assert m["source"] == "none"
    assert m["mass"] == {}
    assert m["order"] == 1


def test_order_never_exceeds_three():
    from app.steps.synthesis import _measured_order
    jid = _scenario()
    _state(jid, "pE", {"hops": {"oA": 3}, "mass": {"3": 50.0}})
    assert _measured_order(jid, ["pE"], {"oA": 90}, db_path=DB)["order"] == 3


def test_hops_follow_the_particle_that_carries_the_state():
    """Branch children get a fresh id. Keying hops to the parent dropped the
    record for every branched timeline, so those outcomes reported no order."""
    from app.db import get_conn
    from app.steps.engine import run_step
    jid = _scenario()
    edges = [{"source": "f0", "target": "f1", "strength": 0.5, "delay_days": 2}]

    def devs(_p):
        return [{"kind": "branch", "probability": 0.9, "impact": 0.9,
                 "delta": {"f0": 10.0}}]

    out = run_step(jid, 1, [{"id": "root", "factor_state": {"f0": 50, "f1": 50},
                             "probability": 1.0}],
                   developments_fn=devs, budget={"steps": 0},
                   causal_edges=edges)
    child = [p for p in out["particles"] if p.get("parent") == "root"][0]
    with get_conn(DB) as c:
        r = c.execute("SELECT causal_hops, causal_source FROM particle_states "
                      "WHERE particle_id=?", (child["id"],)).fetchone()
    assert r is not None, "the branch child has no state row"
    payload = json.loads(r["causal_hops"])
    assert payload["hops"].get("f0") == 0
    assert payload["hops"].get("f1") == 1
    assert payload["mass"]["0"] == pytest.approx(10.0)
    assert r["causal_source"] == "graph"


# ---- the order reaches the reader, and only when it was measured ----

def test_order_label_is_none_without_a_measurement():
    """A stored default of 1 on a pre-causal run must not be presented as a
    finding: three identical "first-order" badges were what the old UI got
    wrong."""
    from app.futures import _order_label
    assert _order_label({"order": 1}, None) is None
    assert _order_label({"order": 1}, {}) is None


def test_order_label_reads_a_measured_distribution():
    from app.futures import _order_label
    lbl = _order_label({"order": 2}, {"0": 5.0, "1": 40.0})
    assert lbl and lbl["n"] == 2
    assert lbl["word"] == "second-order"
    assert "knock-on" in lbl["blurb"]


def test_order_label_rejects_an_out_of_range_value():
    from app.futures import _order_label
    assert _order_label({"order": 7}, {"0": 1.0}) is None


def test_hop_mass_survives_the_artifact_round_trip():
    """_j() coerces its payload to a list of lines, so a JSON *object* like
    hop_mass came back empty and every card lost its order."""
    from app.futures import _load_map
    assert _load_map('{"0": 5.0, "1": 40.0}') == {"0": 5.0, "1": 40.0}
    assert _load_map("{}") is None
    assert _load_map(None) is None
    assert _load_map("not json") is None


def test_rendered_cards_state_the_order():
    from app import create_app
    from app.db import get_conn
    jid = _scenario("ship the app")
    with get_conn(DB) as c:
        c.execute("UPDATE projection_scenarios SET status='COMPLETED' WHERE id=?", (jid,))
        c.execute("INSERT OR REPLACE INTO outcomes(id,scenario_id,name,probability,band,"
                  "summary,explanation,factor_states,\"order\",causal_hops,hop_mass) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  ("ord1", jid, "Outcome 1 · 100% · dominant", 1.0, "dominant",
                   "A calm path.", "It goes well.", "{}", 2, "{}",
                   '{"0": 5.0, "1": 40.0}'))
        c.execute("INSERT OR IGNORE INTO particles(id,scenario_id,probability,"
                  "factor_state,created_step) VALUES('ordp',?,1.0,'{}',40)", (jid,))
        c.execute("INSERT INTO outcome_particle_memberships(outcome_id,particle_id,weight) "
                  "VALUES('ord1','ordp',1.0)")
    body = create_app({"TESTING": True}).test_client().get(
        f"/scenarios/{jid}").get_data(as_text=True)
    assert "oc-order" in body
    assert "second-order" in body.lower()
