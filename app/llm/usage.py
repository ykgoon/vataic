"""Usage accounting: per-call token capture and budget_state reconciliation.

budget_state (projection_jobs.budget_state JSON) is the worker's running
tally. Both are updated by the LLM client on every real provider call.
reconcile_budget() recomputes the tallies from the llm_calls table so jobs
run before usage tracking existed (or resumed after a crash) report real
numbers.
"""
import json

from app.db import get_conn


def _load_budget(c, jid: str) -> dict:
    r = c.execute(
        "SELECT budget_state FROM projection_jobs WHERE id=?", (jid,)).fetchone()
    if r is None:
        return {}
    try:
        b = json.loads(r["budget_state"]) if r["budget_state"] else {}
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}


def _ensure_caps(b: dict) -> dict:
    from app.config import settings
    caps = b.get("caps")
    if not isinstance(caps, dict) or not caps:
        caps = {}
    caps.setdefault("steps", settings.budget_max_steps)
    caps.setdefault("llm_calls", settings.budget_max_llm_calls)
    caps.setdefault("tokens", settings.budget_max_tokens)
    caps.setdefault("branches", settings.budget_max_branches)
    caps.setdefault("runtime_s", settings.budget_max_runtime_s)
    b["caps"] = caps
    return b


def record_llm_usage(job_id: str, phase: str, usage: dict | None,
                     model: str = "", db_path=None) -> None:
    """Add one provider call (+ its token usage) to the job's budget_state."""
    if not job_id:
        return
    u = usage or {}
    with get_conn(db_path) as c:
        b = _ensure_caps(_load_budget(c, job_id))
        b["llm_calls"] = int(b.get("llm_calls", 0) or 0) + 1
        b["tokens"] = int(b.get("tokens", 0) or 0) + int(u.get("total_tokens") or 0)
        b["prompt_tokens"] = int(b.get("prompt_tokens", 0) or 0) + int(u.get("prompt_tokens") or 0)
        b["completion_tokens"] = int(b.get("completion_tokens", 0) or 0) + int(u.get("completion_tokens") or 0)
        c.execute("UPDATE projection_jobs SET budget_state=? WHERE id=?",
                  (json.dumps(b), job_id))


def reconcile_budget(job_id: str, db_path=None) -> dict:
    """Backfill budget_state from the durable llm_calls table.

    Corrects jobs that ran before usage capture existed: llm_calls/tokens are
    re-derived from the stored per-call rows (max with any counted value so a
    healthy job never regresses). Also refreshes caps to include new keys.
    Returns the reconciled budget dict.
    """
    with get_conn(db_path) as c:
        b = _ensure_caps(_load_budget(c, job_id))
        if not b:
            return b
        n_calls, toks, ptok, ctok = c.execute(
            """SELECT COUNT(*), COALESCE(SUM(total_tokens),0),
               COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0)
               FROM llm_calls WHERE job_id=? AND cache_hit=0""", (job_id,)).fetchone()
        b["llm_calls"] = max(int(b.get("llm_calls", 0) or 0), int(n_calls or 0))
        b["tokens"] = max(int(b.get("tokens", 0) or 0), int(toks or 0))
        b["prompt_tokens"] = max(int(b.get("prompt_tokens", 0) or 0), int(ptok or 0))
        b["completion_tokens"] = max(int(b.get("completion_tokens", 0) or 0), int(ctok or 0))
        c.execute("UPDATE projection_jobs SET budget_state=? WHERE id=?",
                  (json.dumps(b), job_id))
        return b


def job_usage_summary(job_id: str, db_path=None) -> dict:
    """Aggregate per-call usage for a job: call counts, tokens, per-phase split."""
    with get_conn(db_path) as c:
        row = c.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(total_tokens),0) AS tokens,
                      COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
                      COALESCE(SUM(completion_tokens),0) AS completion_tokens,
                      SUM(CASE WHEN cache_hit=0 THEN 1 ELSE 0 END) AS real_calls
               FROM llm_calls WHERE job_id=?""", (job_id,)).fetchone()
        phases = [dict(r) for r in c.execute(
            """SELECT phase, COUNT(*) AS calls, COALESCE(SUM(total_tokens),0) AS tokens
               FROM llm_calls WHERE job_id=? GROUP BY phase ORDER BY calls DESC""",
            (job_id,))]
    return {
        "calls": int(row["calls"] or 0),
        "real_calls": int(row["real_calls"] or 0),
        "tokens": int(row["tokens"] or 0),
        "prompt_tokens": int(row["prompt_tokens"] or 0),
        "completion_tokens": int(row["completion_tokens"] or 0),
        "by_phase": phases,
    }
