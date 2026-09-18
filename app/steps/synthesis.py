"""Outcome synthesis: clustering, labels, weights, traces, explanations, bands."""
import json, math, uuid
import numpy as np

BANDS = [(0.4, "dominant"), (0.2, "plausible"), (0.08, "alternative"), (0.0, "tail")]
DISCLAIMER = "These are structured scenario explorations, not predictions."

def band_for(p: float) -> str:
    for t, b in BANDS:
        if p >= t: return b
    return "tail"

def vectorize(particles: list[dict], keys: list[str]) -> np.ndarray:
    return np.array([[p.get("factor_state", {}).get(k, 50.0) for k in keys] for p in particles],
                    dtype=float)

def cluster(particles: list[dict], n_outcomes: int = 7) -> list[list[dict]]:
    if not particles: return []
    keys = sorted({k for p in particles for k in p.get("factor_state", {})})
    if not keys or len(particles) == 1: return [particles]
    X = vectorize(particles, keys)
    k = max(1, min(n_outcomes, len(particles)))
    try:
        from sklearn.cluster import AgglomerativeClustering
        labels = AgglomerativeClustering(n_clusters=k).fit_predict(X)
    except Exception:
        # numpy k-means fallback
        rng = np.random.default_rng(0)
        C = X[rng.choice(len(X), k, replace=False)]
        labels = np.zeros(len(X), dtype=int)
        for _ in range(20):
            d = ((X[:, None, :] - C[None, :, :]) ** 2).sum(-1)
            labels = d.argmin(1)
            for i in range(k):
                if (labels == i).any(): C[i] = X[labels == i].mean(0)
    groups: dict[int, list[dict]] = {}
    for p, lb in zip(particles, labels): groups.setdefault(int(lb), []).append(p)
    return list(groups.values())

def synthesize(job_id: str, particles: list[dict], n_outcomes: int = 7,
               personal: dict | None = None, db_path=None) -> list[dict]:
    from app.db import get_conn
    groups = cluster(particles, n_outcomes)
    total = sum(p.get("probability", 0) for p in particles) or 1.0
    outcomes = []
    for i, g in enumerate(groups):
        w = sum(p.get("probability", 0) for p in g) / total
        rep = max(g, key=lambda p: p.get("probability", 0))
        oid = uuid.uuid4().hex[:12]
        o = {"id": oid, "name": f"Outcome {i+1}", "probability": w, "band": band_for(w),
             "summary": f"Representative path from {len(g)} particle(s).",
             "factor_states": rep.get("factor_state", {}), "causal_path": [],
             "indicators": [], "assumptions": [], "risks": [],
             "explanation": "", "representative": rep.get("id", ""),
             "alternatives": [p.get("id") for p in g if p.get("id") != rep.get("id")]}
        if personal:
            o["enrichment"] = {"decision_levers": [], "protective_actions": [],
                "warning_signs": [], "support_needed": [], "wellbeing_risks": [],
                "decision_map": personal.get("decision_map", [])}
        outcomes.append(o)
    # normalize to 1
    s = sum(o["probability"] for o in outcomes) or 1.0
    for o in outcomes: o["probability"] /= s; o["band"] = band_for(o["probability"])
    with get_conn(db_path) as c:
        # stub member particles so membership FK always resolves
        for g in groups:
            for p in g:
                if p.get("id"):
                    c.execute("""INSERT OR IGNORE INTO particles(id,job_id,probability,factor_state,created_step)
                      VALUES(?,?,?, ?,0)""",
                      (p["id"], job_id, p.get("probability", 0),
                       json.dumps(p.get("factor_state", {}))))
        for o in outcomes:
            c.execute("""INSERT OR REPLACE INTO outcomes(id,job_id,name,probability,band,summary,
              factor_states,causal_path,indicators,assumptions,risks,explanation,
              representative_particle,enrichment) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (o["id"], job_id, o["name"], o["probability"], o["band"], o["summary"],
               json.dumps(o["factor_states"]), json.dumps(o["causal_path"]),
               json.dumps(o["indicators"]), json.dumps(o["assumptions"]),
               json.dumps(o["risks"]), o["explanation"], o["representative"],
               json.dumps(o.get("enrichment", {}))))
            for pid in [o["representative"]] + o["alternatives"]:
                if pid: c.execute("""INSERT OR IGNORE INTO outcome_particle_memberships
                  (outcome_id,particle_id,weight) VALUES(?,?,?)""", (o["id"], pid, o["probability"]))
    return outcomes

def explain(outcome: dict, use_llm=False, job_id="", db_path=None) -> dict:
    if use_llm:
        try:
            from app.llm.client import LLMClient, model_for_phase
            r = LLMClient(db_path=db_path).chat(job_id or "x", "explanation", [
                {"role": "system", "content": "Explain outcome JSON: explanation, indicators, assumptions, risks."},
                {"role": "user", "content": json.dumps(outcome)[:3000]}],
                model=model_for_phase("explanation"))["json"]
            outcome.update({k: r.get(k, outcome.get(k)) for k in
                            ("explanation", "indicators", "assumptions", "risks")})
        except Exception:
            pass
    if not outcome.get("explanation"):
        outcome["explanation"] = (f"{outcome['name']} ({outcome['probability']:.0%}, {outcome['band']}): "
            f"{outcome.get('summary','')} {DISCLAIMER}")
    return outcome
