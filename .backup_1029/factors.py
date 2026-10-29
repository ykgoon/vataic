"""Factor selection + causal graph + world state (tasks 7.x)."""
import json, math, itertools
from app.db import get_conn
from app.jobs.repositories import idem_key, idem_get, idem_put

IMPACT_SURFACES = ["physical_environment","biology","population","resources","economy",
 "governance","security","technology","information","culture","psychology","institutions",
 "geography","time"]
PERSONAL_CATS = ["financial_resilience","income_trajectory","skill_development","reputation",
 "network_access","emotional_regulation","motivation","health_energy","family_support",
 "time_availability","habit_consistency","decision_confidence","optionality","environmental_opportunity"]

def importance(direct: float, cascade: float, uncert: float, policy: float, obs: float) -> float:
    return 0.35*direct + 0.25*cascade + 0.15*uncert + 0.15*policy + 0.10*obs

def select_factors(job_id: str, candidates: list[dict], scope="macro", db_path=None) -> dict:
    from app.config import settings
    cap = settings.factor_max_personal if scope == "personal" else settings.factor_max_macro
    scored = sorted(candidates, key=lambda f: f.get("importance", 0), reverse=True)
    kept, rest = scored[:cap], scored[cap:]
    result = {"factors": kept, "discarded": rest,
              "watchlist": [f.get("name", "") for f in rest[:5]]}
    with get_conn(db_path) as c:
        c.execute("""INSERT INTO factor_sets(job_id,factors,discarded,watchlist)
          VALUES(?,?,?,?)""", (job_id, json.dumps(kept), json.dumps(rest),
                                json.dumps(result["watchlist"])))
    return result

def default_candidates(premise: str, scope="macro") -> list[dict]:
    cats = PERSONAL_CATS if scope == "personal" else IMPACT_SURFACES
    out = []
    for i, cat in enumerate(cats):
        out.append({"id": f"f{i}", "name": cat.replace("_", " ").title(), "surface": cat,
          "type": "index", "range": [0, 100], "current": 50.0, "baseline": 50.0,
          "trend": 0.0, "momentum": 0.0, "volatility": 0.3,
          "importance": importance(0.7 - i*0.02, 0.6 - i*0.02, 0.5, 0.4, 0.6)})
    return out

# ---- causal graph ----
def validate_edge(edge: dict, factor_ids: set[str]) -> str | None:
    if edge.get("source") not in factor_ids or edge.get("target") not in factor_ids:
        return "unknown factor"
    s = edge.get("strength", 0)
    if not (-1.0 <= s <= 1.0): return "implausible strength"
    if edge.get("delay_days", 0) is None: return "missing delay"
    return None

def has_cycle(edges: list[dict]) -> bool:
    g: dict[str, list[str]] = {}
    for e in edges: g.setdefault(e["source"], []).append(e["target"])
    seen: dict[str, int] = {}
    def dfs(n: str, stack: set[str]) -> bool:
        if n in stack: return True
        if seen.get(n): return False
        stack.add(n)
        for m in g.get(n, []):
            if dfs(m, stack): return True
        stack.remove(n); seen[n] = 1
        return False
    return any(dfs(n, set()) for n in g)

def _default_edges(factor_ids: list) -> list[dict]:
    """Deterministic causal edges forming a small acyclic graph.

    No LLM. Builds a chain plus a few cross-edges so the graph is connected
    but acyclic (a chain is acyclic; cross-edges only go forward in index
    order, which preserves acyclicity). Strengths and delays are deterministic
    from the factor ids.
    """
    import hashlib
    ids = [f for f in factor_ids if f]
    n = len(ids)
    if n < 2:
        return []
    edges = []
    def strength(i, j):
        h = int.from_bytes(hashlib.sha256(f"{i}|{j}".encode()).digest()[:4], "big")
        # signed, magnitude 0.2..0.8
        sign = 1.0 if h % 2 else -1.0
        return round(sign * (0.2 + 0.6 * (h % 100) / 100.0), 2)
    def delay(i, j):
        h = int.from_bytes(hashlib.sha256(f"d{i}|{j}".encode()).digest()[:4], "big")
        return 1 + (h % 30)
    # chain: i -> i+1
    for i in range(n - 1):
        edges.append({"source": ids[i], "target": ids[i + 1],
                      "strength": strength(ids[i], ids[i + 1]),
                      "delay_days": delay(ids[i], ids[i + 1]), "polarity": "+"})
    # a few forward cross-edges (i -> j, j > i+1) for connectivity
    for i in range(n - 2):
        j = i + 2
        if j < n:
            edges.append({"source": ids[i], "target": ids[j],
                          "strength": strength(ids[i], ids[j]),
                          "delay_days": delay(ids[i], ids[j]), "polarity": "+"})
    return edges


def build_causal_graph(job_id: str, factors: list[dict], proposed: list[dict] | None = None,
                       db_path=None) -> dict:
    ids = {f["id"] for f in factors}
    edges, failures = [], []
    proposed = proposed or _default_edges([f["id"] for f in factors])
    for e in proposed:
        err = validate_edge(e, ids)
        if err:
            failures.append({"edge": e, "error": err})
        else:
            edges.append(e)
    # critic: flag self-contradictory cycles (same pair opposite polarity, no condition change)
    flagged = has_cycle(edges)
    with get_conn(db_path) as c:
        c.execute("INSERT INTO causal_graphs(job_id,nodes,edges) VALUES(?,?,?)",
                  (job_id, json.dumps([f["id"] for f in factors]), json.dumps(edges)))
    return {"edges": edges, "failures": failures, "cycle_flagged": flagged}

def critic_revise(graph: dict) -> dict:
    """Critic pass: drop contradictory opposite-polarity duplicates, request revision."""
    seen: dict[tuple, dict] = {}
    revised = []
    for e in graph.get("edges", []):
        k = (e["source"], e["target"])
        if k in seen and seen[k].get("polarity") != e.get("polarity") \
           and not e.get("condition") and not seen[k].get("condition"):
            seen[k] = e  # keep latest, flag revision
            graph["revision_requested"] = True
            continue
        seen[k] = e
    graph["edges"] = list(seen.values())
    return graph

def init_world_state(job_id: str, factors: list[dict], db_path=None) -> dict:
    vals = {f["id"]: max(0.0, min(100.0, float(f.get("current", 50)))) for f in factors}
    with get_conn(db_path) as c:
        c.execute("""INSERT OR REPLACE INTO world_states(job_id,step_index,time_label,factor_values)
          VALUES(?,0,'T0',?)""", (job_id, json.dumps(vals)))
    return vals
