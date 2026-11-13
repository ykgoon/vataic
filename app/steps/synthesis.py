"""Outcome synthesis: clustering, labels, weights, traces, explanations, bands."""
import json, math, uuid
import numpy as np

from app.db import get_conn
from app.narrate import (arc, delta_of, end_state, level_word, movement,
                         onset, reversal, sanitize_prose, stays)

BANDS = [(0.4, "dominant"), (0.2, "plausible"), (0.08, "alternative"), (0.0, "tail")]
DISCLAIMER = "These are structured scenario explorations, not predictions."

def band_for(p: float) -> str:
    for t, b in BANDS:
        if p >= t: return b
    return "tail"

def _human_factor_names(db_path=None) -> dict:
    """Map factor id -> human name from the most recent factor_sets rows."""
    try:
        with get_conn(db_path) as c:
            rows = c.execute("SELECT factors FROM factor_sets ORDER BY id DESC LIMIT 12").fetchall()
    except Exception:
        return {}
    names: dict[str, str] = {}
    for r in rows:
        try:
            for f in json.loads(r["factors"]):
                if isinstance(f, dict) and f.get("id"):
                    names.setdefault(f["id"], (f.get("name") or f["id"]).strip())
        except Exception:
            continue
    return names


def _pct(x: float) -> str:
    """Human probability string (e.g. '3.1%'), never raw float noise."""
    try:
        v = float(x) * 100.0
    except Exception:
        return "?"
    if v >= 10:
        return f"{v:.0f}%"
    if v >= 1:
        return f"{v:.1f}%"
    if v >= 0.1:
        return f"{v:.2f}%"
    return f"<0.1%"


def _direction(v: float) -> str:
    """Plain-English standing of a factor value relative to its starting level.

    Was "well above baseline"/"below baseline": "baseline" is storage
    vocabulary (every factor starts at 50), not something a reader knows.
    """
    return end_state(delta_of(v))


def _factor_label(fs: dict, names: dict | None = None) -> str:
    """Deterministic short label from a particle's factor state (no LLM)."""
    if not fs:
        return ""
    names = names or {}
    def fmt(v):
        try: return f"{float(v):.0f}"
        except Exception: return str(v)
    parts = []
    for k, v in sorted(fs.items(), key=lambda kv: abs(float(kv[1]) - 50.0), reverse=True):
        if not isinstance(v, (int, float)):
            continue
        parts.append(f"{names.get(k, k)}: {level_word(delta_of(v))}")
    return " / ".join(parts[:4]) if parts else ""


def _summarize(premise: str, rep: dict, g: list[dict], names: dict) -> str:
    """One-sentence readable summary of what this outcome path looks like."""
    fs = rep.get("factor_state", {})
    movers = [(names.get(k, k), float(v)) for k, v in fs.items()
              if isinstance(v, (int, float)) and abs(float(v) - 50.0) >= 5.0]
    movers.sort(key=lambda t: abs(t[1] - 50.0), reverse=True)
    if movers:
        top = "; ".join(f"{n} {end_state(delta_of(v))}" for n, v in movers[:3])
        return (f"By the end of the horizon, {top}. "
                f"This path formed from {len(g)} evolving timeline(s) of the premise.")
    return (f"Factors stay close to their starting levels throughout the horizon; "
            f"the premise plays out without major shifts ({len(g)} timeline(s) explored).")


def _narrative(outcome: dict, premise: str, names: dict) -> str:
    """Plain-language explanation a reader can act on (no LLM). Ties the factor
    movements back to the premise instead of restating bare numbers."""
    band_txt = {"dominant": "the most likely path",
                "plausible": "a credible path",
                "alternative": "a secondary path",
                "tail": "a low-probability path"}.get(outcome.get("band"), "a possible path")
    lines = []
    if premise:
        lines.append(f"For the premise “{premise[:200]}”, this is {band_txt} "
                     f"carrying about {_pct(outcome.get('probability', 0))} "
                     f"of the explored probability mass.")
    else:
        lines.append(f"This outcome is {band_txt} with about {_pct(outcome.get('probability', 0))} "
                     f"of the explored probability mass.")
    movers = sorted(outcome.get("factor_states", {}).items(),
                    key=lambda kv: abs(float(kv[1]) - 50.0), reverse=True
                    ) if isinstance(outcome.get("factor_states"), dict) else []
    movers = [(names.get(k, k), float(v)) for k, v in movers
              if isinstance(v, (int, float))][:4]
    if movers:
        tops = "; ".join(f"{n} {movement(delta_of(v))}" for n, v in movers[:2])
        rest = [n for n, _ in movers[2:]]
        lines.append(f"In this future, {tops}"
                     + (", with " + " and ".join(rest) + " also " +
                        ("moving" if len(rest) > 1 else "moving") if rest else "")
                     + ".")
    inds = outcome.get("indicators") or []
    if inds:
        lines.append("Watch for: " + ", ".join(inds[:3]) + ".")
    risks = outcome.get("risks") or []
    if risks:
        lines.append("Risks: " + "; ".join(risks[:2]) + ".")
    lines.append(DISCLAIMER)
    return " ".join(lines)

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

def _deterministic_enrich(o: dict, db_path=None, names: dict | None = None) -> dict:
    """Populate causal_path, indicators, assumptions, risks deterministically
    from the representative particle's factor state and the scenario's causal graph.

    No LLM. Keeps outcomes explainable from stored traces even without an
    LLM provider.
    """
    names = names or _human_factor_names(db_path)
    fs = o.get("factor_states", {}) or {}
    numeric = {k: v for k, v in fs.items()
               if isinstance(v, (int, float))}
    # Ranked by distance from the 50.0 baseline; skip factors that never moved.
    ranked = [(k, v) for k, v in
              sorted(numeric.items(), key=lambda kv: abs(float(kv[1]) - 50.0),
                     reverse=True) if abs(float(v) - 50.0) >= 1.0]
    # Every line below describes a movement in words: how it starts, how it
    # travels, where it lands. The 0-100 index, the 50 midpoint and the word
    # "baseline" are storage details and never reach the reader.
    o["indicators"] = [f"{names.get(k, k)} {onset(delta_of(v))}"
                       for k, v in ranked[:3]]
    # Causal path: the pivotal factors, ordered by magnitude, each with its arc.
    o["causal_path"] = [f"{names.get(k, k)} {arc(delta_of(v))}"
                        for k, v in ranked[:5]]
    # Assumptions: factor states that anchor this outcome.
    o["assumptions"] = [f"{names.get(k, k)} {stays(delta_of(v))}"
                        for k, v in ranked[:3]] or \
                       ["No factor moves materially; the horizon stays calm"]
    # Risks: what would walk this path back, plus a generic tail-risk note.
    o["risks"] = [f"{names.get(k, k)} {reversal(delta_of(v))} would undo this path"
                  for k, v in ranked[:3]] or \
                 ["A large external shock could still reshuffle all factors"]
    if o.get("band") in ("tail", "alternative"):
        o["risks"].append("Low-probability outcome; treat as a stress case, not a plan.")
    return o


def llm_cluster_labels(scenario_id: str, premise: str, groups: list[list[dict]],
                       names: dict, db_path=None) -> dict | None:
    """LLM naming of clustered outcomes (llm-gateway ClusterOut phase).
    Returns {outcome-index: {name, summary}} or None when unavailable."""
    try:
        from app.llm.client import LLMClient, model_for_phase
        def _desc(g):
            fs = g[0].get("factor_state", {})
            return ", ".join(f"{names.get(k, k)} {level_word(delta_of(v))}"
                             for k, v in sorted(fs.items(),
                                                key=lambda kv: abs(float(kv[1]) - 50.0),
                                                reverse=True)[:6])
        payload = [{"cluster": i, "timelines": len(g), "end_state": _desc(g)}
                   for i, g in enumerate(groups)]
        out = LLMClient(db_path=db_path).chat(scenario_id, "cluster", [
            {"role": "system", "content":
             "Given a scenario premise and clustered end-states of explored timelines, "
             "name each cluster and give a one-sentence summary of how that future "
             "unfolds for the premise. The end-state digest describes each factor "
             "relative to where it started, so never quote index numbers, fractions "
             "like 62/100, or the word 'baseline' in what you return. "
             "Respond ONLY with JSON of shape: "
             "{\"labels\": [{\"cluster\": int, \"name\": str, \"summary\": str}]}. "
             "Names must be specific to the premise (what actually happens, e.g. "
             "'SaaS becomes commodity data feed', 'Agent economy locks in the SaaS'), "
             "never generic. Summaries must say how the premise turns out."},
            {"role": "user", "content":
             ("Premise: " + premise[:600] + "\n" if premise else "") +
             json.dumps(payload)[:3000]}],
            model=model_for_phase("cluster"))["json"]
        labels = {}
        for l in out.get("labels", []):
            if isinstance(l, dict) and isinstance(l.get("cluster"), int):
                labels[l["cluster"]] = {"name": str(l.get("name", ""))[:80],
                                        "summary": str(l.get("summary", ""))[:300]}
        return labels or None
    except Exception:
        return None


def synthesize(scenario_id: str, particles: list[dict], n_outcomes: int = 7,
               personal: dict | None = None, db_path=None,
               premise: str = "") -> list[dict]:
    from app.db import get_conn
    groups = cluster(particles, n_outcomes)
    llm_labels = llm_cluster_labels(scenario_id, premise, groups, names=_human_factor_names(db_path),
                                    db_path=db_path) if (premise and len(groups) > 1) else None
    total = sum(p.get("probability", 0) for p in particles) or 1.0
    names = _human_factor_names(db_path)
    outcomes = []
    for i, g in enumerate(groups):
        w = sum(p.get("probability", 0) for p in g) / total
        rep = max(g, key=lambda p: p.get("probability", 0))
        oid = uuid.uuid4().hex[:12]
        fs = rep.get("factor_state", {})
        lbl = (llm_labels or {}).get(i, {})
        o = {"id": oid, "probability": w, "band": band_for(w),
             "summary": (lbl.get("summary") or
                         _summarize(premise, rep, g, names)),
             "llm_named": bool(lbl.get("name")),
             "factor_states": rep.get("factor_state", {}), "causal_path": [],
             "indicators": [], "assumptions": [], "risks": [],
             "explanation": "", "representative": rep.get("id", ""),
             "alternatives": [p.get("id") for p in g if p.get("id") != rep.get("id")]}
        if personal:
            o["enrichment"] = {"decision_levers": [], "protective_actions": [],
                "warning_signs": [], "support_needed": [], "wellbeing_risks": [],
                "decision_map": personal.get("decision_map", [])}
        outcomes.append(o)
    # normalize to 1, then name by final rank so cards read like a report
    s = sum(o["probability"] for o in outcomes) or 1.0
    for o in outcomes:
        o["probability"] /= s
        o["band"] = band_for(o["probability"])
    ranked = sorted(outcomes, key=lambda o: -o["probability"])
    for rank, o in enumerate(ranked, start=1):
        o["name"] = f"Outcome {rank} · {_pct(o['probability'])} · {o['band']}"
    # LLM cluster labels replace generic numbering when available
    if llm_labels:
        for i, o in enumerate(outcomes):
            lbl = llm_labels.get(i, {})
            if lbl.get("name"):
                o["name"] = lbl["name"]
    outcomes.sort(key=lambda o: -o["probability"])
    # Deterministic enrichment (uses final probability + band).
    for o in outcomes:
        _deterministic_enrich(o, db_path, names)
    with get_conn(db_path) as c:
        # stub member particles so membership FK always resolves
        for g in groups:
            for p in g:
                if p.get("id"):
                    c.execute("""INSERT OR IGNORE INTO particles(id,scenario_id,probability,factor_state,created_step)
                      VALUES(?,?,?, ?,0)""",
                      (p["id"], scenario_id, p.get("probability", 0),
                       json.dumps(p.get("factor_state", {}))))
        for o in outcomes:
            c.execute("""INSERT OR REPLACE INTO outcomes(id,scenario_id,name,probability,band,summary,
              factor_states,causal_path,indicators,assumptions,risks,explanation,
              representative_particle,enrichment) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (o["id"], scenario_id, o["name"], o["probability"], o["band"], o["summary"],
               json.dumps(o["factor_states"]), json.dumps(o["causal_path"]),
               json.dumps(o["indicators"]), json.dumps(o["assumptions"]),
               json.dumps(o["risks"]), o["explanation"], o["representative"],
               json.dumps(o.get("enrichment", {}))))
            for pid in [o["representative"]] + o["alternatives"]:
                if pid: c.execute("""INSERT OR IGNORE INTO outcome_particle_memberships
                  (outcome_id,particle_id,weight) VALUES(?,?,?)""", (o["id"], pid, o["probability"]))
    return outcomes

def explain(outcome: dict, use_llm=False, scenario_id="", db_path=None,
            premise: str = "") -> dict:
    if use_llm:
        try:
            from app.llm.client import LLMClient, model_for_phase
            r = LLMClient(db_path=db_path).chat(scenario_id or "x", "explanation", [
                {"role": "system", "content":
                 "You are explaining one scenario-projection outcome to a non-technical reader. "
                 "Given the outcome JSON, return JSON with keys: explanation (a clear paragraph: "
                 "what this future is, the concrete story of how the world gets there, and what "
                 "it means for the premise question — reference the premise's actual subject and "
                 "question; never write generic filler), "
                 "indicators (list of early signals specific to the premise), assumptions (list), risks (list). "
                 "Use plain language and full factor names; never raw factor ids. "
                 "Never quote index numbers, never write values like 35/100 or "
                 "'from 50 to 35', and never say 'baseline': factors are scored on an "
                 "internal 0-100 scale whose midpoint means 'where it started', and "
                 "those numbers are meaningless to the reader. Describe each movement "
                 "in words instead - how far it moved (slightly, noticeably, sharply, "
                 "dramatically) and which direction, relative to where it started."},
                {"role": "user", "content":
                 (("Premise: " + premise[:600] + "\n") if premise else "") +
                 json.dumps(outcome)[:3000]}],
                model=model_for_phase("explanation"))["json"]
            if isinstance(r, dict) and r.get("explanation"):
                outcome.update({k: r.get(k, outcome.get(k)) for k in
                                ("explanation", "indicators", "assumptions", "risks")})
                # The prompt forbids quoting the index, but a model can still
                # echo it. Keep the prose, rewrite the numbers.
                for k in ("explanation", "indicators", "assumptions", "risks"):
                    v = outcome.get(k)
                    if isinstance(v, str):
                        outcome[k] = sanitize_prose(v)
                    elif isinstance(v, list):
                        outcome[k] = [sanitize_prose(x) if isinstance(x, str) else x
                                      for x in v]
        except Exception:
            pass
    if not outcome.get("explanation"):
        names = _human_factor_names(db_path)
        outcome["explanation"] = _narrative(outcome, premise, names)
    return outcome
