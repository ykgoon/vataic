"""Seed defaults for settings table. Idempotent.

DB rows are the primary config source now; seeding ensures the settings page
has a visible row per editable key on first run. Env vars still take
precedence over an *untouched* seed row (see routes/settings._effective).
"""
from app.db import get_conn

DEFAULTS = {
  "llm.provider": "openai-compatible",
  "llm.base_url": "http://localhost:11434/v1",
  "llm.primary_model": "",
  "llm.strong_model": "",
  "llm.cheap_model": "",
  "llm.timeout_s": "120",
  "llm.max_attempts": "4",
  "budget.max_steps": "40",
  "budget.max_runtime_s": "28800",
  "budget.max_branches": "20",
  "beam.width": "12", "beam.reserved_divergent": "3",
  "merge.distance_threshold": "8.0",
  "branch.prob_threshold": "0.15",
  "tail_risk.impact_threshold": "0.8",
  "factors.max_macro": "15", "factors.max_personal": "18",
  "step.min_days": "7", "step.max_days": "365",
  "epoch.interval_steps": "4",
  "context.token_budget": "6000",
}

def seed(db_path=None) -> int:
    n = 0
    with get_conn(db_path) as c:
        for k, v in DEFAULTS.items():
            cur = c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
            n += cur.rowcount
    return n
