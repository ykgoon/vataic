"""Seed defaults for settings table (task 2.5). Idempotent."""
import json
from app.db import get_conn

DEFAULTS = {
  "llm.provider": "openai-compatible",
  "llm.base_url": "http://localhost:11434/v1",
  "llm.primary_model": "gpt-4o-mini",
  "llm.strong_model": "",
  "llm.cheap_model": "",
  "budget.max_steps": "40", "budget.max_llm_calls": "500",
  "budget.max_tokens": "500000", "budget.max_runtime_s": "28800",
  "budget.max_branches": "20",
  "beam.width": "12", "beam.reserved_divergent": "3",
  "merge.distance_threshold": "8.0",
  "factors.max_macro": "15", "factors.max_personal": "18",
  "step.min_days": "7", "step.max_days": "365",
}

def seed(db_path=None) -> int:
    n = 0
    with get_conn(db_path) as c:
        for k, v in DEFAULTS.items():
            cur = c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
            n += cur.rowcount
    return n
