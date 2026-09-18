"""Memory compression: 3-tier model, epoch summaries, turning points, truncation."""
import json
from app.db import get_conn

def scenario_card(job_id: str, db_path=None) -> dict:
    with get_conn(db_path) as c:
        r = c.execute("SELECT * FROM scenario_inputs WHERE job_id=?", (job_id,)).fetchone()
        return dict(r) if r else {}

def epoch_boundary(step: int, scope="macro", db_path=None) -> bool:
    from app.config import settings
    interval = settings.epoch_interval_steps
    return step > 0 and step % interval == 0

def historian_summary(job_id: str, step: int, events: list, use_llm=False, db_path=None) -> dict:
    from app.jobs.repositories import idem_key, idem_get, idem_put
    key = idem_key(job_id, "epoch", step, "", str(len(events)))
    c = idem_get(key, db_path)
    if c: return c
    if use_llm:
        try:
            from app.llm.client import LLMClient, model_for_phase
            out = LLMClient(db_path=db_path).chat(job_id, "epoch", [
                {"role": "system", "content": "Summarize epoch events into JSON: summary, turning_points."},
                {"role": "user", "content": json.dumps(events)[:4000]}], model=model_for_phase("epoch"))["json"]
        except Exception:
            out = {"summary": "; ".join(str(e)[:120] for e in events[:5]), "turning_points": []}
    else:
        out = {"summary": "; ".join(str(e)[:120] for e in events[:5]), "turning_points": []}
    idem_put(key, out, db_path)
    return out

def add_turning_point(job_id: str, step: int, text: str, db_path=None) -> None:
    with get_conn(db_path) as c:
        r = c.execute("SELECT turning_points FROM projection_steps WHERE job_id=? AND step_index=?",
                      (job_id, step)).fetchone()
        tp = json.loads(r["turning_points"]) if r else []
        tp.append(text)
        c.execute("""INSERT INTO projection_steps(job_id,step_index,turning_points)
          VALUES(?,?,?) ON CONFLICT(job_id,step_index) DO UPDATE SET turning_points=excluded.turning_points""",
          (job_id, step, json.dumps(tp)))

def build_context(job_id: str, particle: dict, step: int, causal: dict,
                  epoch_summary: dict | None, turning_points: list[str],
                  db_path=None) -> dict:
    """Compressed context: card, time, factor state, turning points, epoch summary, causal digest."""
    from app.config import settings
    card = scenario_card(job_id, db_path)
    ctx = {"scenario_card": {"premise": card.get("premise", ""), "scope": card.get("premise", ""),
            "reality_mode": card.get("reality_mode", "grounded"),
            "assumptions": json.loads(card.get("assumptions", "[]") if isinstance(card.get("assumptions"), str) else "[]")},
           "current_time": f"step-{step}",
           "factor_state": particle.get("factor_state", {}),
           "pinned_turning_points": turning_points,
           "epoch_summary": (epoch_summary or {}).get("summary", ""),
           "causal_digest": [{"s": e.get("source"), "t": e.get("target"),
                              "p": e.get("polarity")} for e in (causal.get("edges", [])[:20])],
           "task": "Propose next developments as JSON."}
    # token-budget truncation: shrink epoch summary first (9.4)
    est = len(json.dumps(ctx)) // 4
    while est > settings.context_token_budget and ctx["epoch_summary"]:
        ctx["epoch_summary"] = ctx["epoch_summary"][: len(ctx["epoch_summary"]) // 2]
        est = len(json.dumps(ctx)) // 4
    return ctx
