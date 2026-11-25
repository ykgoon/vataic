"""Usage accounting: per-call ledger reads for the results/detail panels.

The per-call record in the `llm_calls` table is kept because two things depend
on it and neither is a budget: it is the LLM response cache (cache_key), and it
is the evidence of a real provider run that the honest-completion gate checks.

What used to live here — a running llm_calls/tokens tally in budget_state and
caps that stopped a run once it hit them — was removed at the operator's
request: a run must not be cut short or blocked by how many requests it made.
"""
import json

from app.db import get_conn


def _load_budget(c, jid: str) -> dict:
    r = c.execute(
        "SELECT budget_state FROM projection_scenarios WHERE id=?", (jid,)).fetchone()
    if r is None:
        return {}
    try:
        b = json.loads(r["budget_state"]) if r["budget_state"] else {}
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}


def reconcile_budget(scenario_id: str, db_path=None) -> dict:
    """Return the scenario's budget_state, dropping any stale spend tallies.

    Rows written before the LLM cap was removed can still carry llm_calls /
    token counters and llm_calls / token caps. They are dropped on read so the
    schema reflects the current rules (no provider-spend ceiling) and old
    values can never re-arm a limit that no longer exists.
    """
    with get_conn(db_path) as c:
        b = _load_budget(c, scenario_id)
        if not b:
            return b
        caps = b.get("caps")
        if isinstance(caps, dict):
            for k in ("llm_calls", "tokens"):
                caps.pop(k, None)
        for k in ("llm_calls", "tokens", "prompt_tokens", "completion_tokens"):
            b.pop(k, None)
        c.execute("UPDATE projection_scenarios SET budget_state=? WHERE id=?",
                  (json.dumps(b), scenario_id))
        return b


def scenario_usage_summary(scenario_id: str, db_path=None) -> dict:
    """Aggregate per-call usage for a scenario: call counts, tokens, per-phase split."""
    with get_conn(db_path) as c:
        row = c.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(total_tokens),0) AS tokens,
                      COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
                      COALESCE(SUM(completion_tokens),0) AS completion_tokens,
                      SUM(CASE WHEN cache_hit=0 THEN 1 ELSE 0 END) AS real_calls
               FROM llm_calls WHERE scenario_id=?""", (scenario_id,)).fetchone()
        phases = [dict(r) for r in c.execute(
            """SELECT phase, COUNT(*) AS calls, COALESCE(SUM(total_tokens),0) AS tokens
               FROM llm_calls WHERE scenario_id=? GROUP BY phase ORDER BY calls DESC""",
            (scenario_id,))]
    return {
        "calls": int(row["calls"] or 0),
        "real_calls": int(row["real_calls"] or 0),
        "tokens": int(row["tokens"] or 0),
        "prompt_tokens": int(row["prompt_tokens"] or 0),
        "completion_tokens": int(row["completion_tokens"] or 0),
        "by_phase": phases,
    }
