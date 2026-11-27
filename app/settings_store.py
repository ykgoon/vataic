"""Settings persistence: the `settings` table is the single config source.

The web UI writes here; app.config.Settings reads these rows (with env as
bootstrap fallback for values that have no row yet).

The LLM API key is a stored setting too (llm.api_key): editable on the
settings page, but never rendered back — the form shows a masked state
only, and a save keeps the existing key unless a new value is typed. The
Flask secret stays env-only.
"""
import json
import re
import sqlite3

import requests

from app.db import get_conn

# Editable keys: (label, help text, input kind)
ALLOWED = {
    "llm.api_key": "API key for the provider (write-only)",
    "llm.provider": "Provider id",
    "llm.base_url": "Base URL of the OpenAI-compatible API (…/v1)",
    "llm.primary_model": "Model used when a phase has no role model (blank = provider default)",
    "llm.strong_model": "Model for parse/causal/step/explanation (blank = primary)",
    "llm.cheap_model": "Model for epoch/cluster (blank = primary)",
    "llm.timeout_s": "Per-request timeout (seconds)",
    "llm.max_attempts": "Retry attempts per LLM call",
    "budget.max_steps": "Max steps per scenario",
    "budget.max_branches": "Max branches per scenario",
    "budget.max_runtime_s": "Max runtime per scenario (seconds)",
    "beam.width": "Beam width (particles kept per step)",
    "beam.reserved_divergent": "Beam slots reserved for divergent particles",
    "merge.distance_threshold": "Merge distance threshold",
    "branch.prob_threshold": "Branch probability threshold",
    "tail_risk.impact_threshold": "Tail-risk impact threshold",
    "factors.max_macro": "Factor cap for macro scenarios",
    "factors.max_personal": "Factor cap for personal scenarios",
    "step.min_days": "Minimum step length (days)",
    "step.max_days": "Maximum step length (days)",
    "epoch.interval_steps": "Steps between epoch summaries",
    "context.token_budget": "Context token budget for LLM prompts",
}

_INT_KEYS = {k for k in ALLOWED if any(
    t in k for t in ("steps", "max_attempts", "runtime", "width", "cap", "_days",
                     "interval", "budget", "timeout"))}
_FLOAT_KEYS = {"merge.distance_threshold", "branch.prob_threshold",
               "tail_risk.impact_threshold"}


def coerce(key: str, raw: str) -> tuple[bool, str]:
    """Validate + normalise one submitted value. Returns (ok, value_or_error)."""
    v = (raw or "").strip()
    if key in _INT_KEYS:
        try:
            return True, str(int(float(v)))
        except ValueError:
            return False, "must be a whole number"
    if key in _FLOAT_KEYS:
        try:
            return True, str(float(v))
        except ValueError:
            return False, "must be a number"
    if key == "llm.base_url":
        if v and not re.match(r"^https?://", v):
            return False, "must start with http:// or https://"
    if key == "llm.provider" and v and not re.match(r"^[a-z0-9-]+$", v):
        return False, "lowercase letters, digits and dashes only"
    return True, v


def save_many(form: dict, db_path=None) -> tuple[list[str], list[str]]:
    """Write editable settings from a form dict {key: value}.

    Returns (saved_keys, errors as "key: message" strings). Unknown keys and
    secret-ish keys are silently refused (never stored).
    """
    saved, errors = [], []
    for key in ALLOWED:
        if key not in form:
            continue
        if key == "llm.api_key":
            # Write-only: an empty submit keeps the stored key; a new value
            # replaces it. Never echoed back into the form or flashes.
            v = (form[key] or "").strip()
            if not v:
                continue
            def _once(k=key, val=v):
                with get_conn(db_path) as c:
                    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, val))
            try:
                _once()
                saved.append(key)
            except sqlite3.OperationalError:
                errors.append(f"{key}: database is locked, value not saved")
            continue
        ok, v = coerce(key, form[key])
        if not ok:
            errors.append(f"{key}: {v}")
            continue

        def _once(k=key, val=v):
            with get_conn(db_path) as c:
                c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, val))
        try:
            _once()
            saved.append(key)
        except sqlite3.OperationalError:
            errors.append(f"{key}: database is locked, value not saved")
    return saved, errors


def probe_llm(base_url: str, api_key: str, model: str, timeout_s: float = 6.0) -> dict:
    """Test the configured LLM provider: reachability + a real completion.

    1) GET /models — proves the endpoint is an OpenAI-compatible API surface.
    2) POST /chat/completions — proves the key and model actually work.

    Returns {"ok", "stage", "detail", "models", "latency_ms"}.
    """
    import time as _time

    base = (base_url or "").rstrip("/")
    out = {"ok": False, "stage": "url", "detail": "no base URL configured",
           "models": [], "latency_ms": 0}
    if not base:
        return out
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # --- stage 1: /models ---
    t0 = _time.monotonic()
    try:
        r = requests.get(base + "/models", headers=headers, timeout=timeout_s)
    except Exception as e:
        out["detail"] = f"unreachable: {type(e).__name__}: {e}"
        return out
    out["latency_ms"] = int((_time.monotonic() - t0) * 1000)
    if r.status_code == 404:
        out.update(stage="chat", ok=False,
                   detail="not an OpenAI-compatible API (GET /models → 404); check the base URL ends in /v1")
        return out
    if r.status_code in (401, 403):
        out.update(stage="auth", ok=False,
                   detail=f"authentication failed (HTTP {r.status_code}) — check the API key")
        return out
    if r.status_code != 200:
        out.update(stage="models", ok=False, detail=f"GET /models → HTTP {r.status_code}")
        return out
    try:
        ids = [str(m.get("id", "")) for m in (r.json().get("data") or [])]
    except Exception:
        ids = []
    out["models"] = ids
    if not ids:
        out.update(stage="chat", ok=False,
                   detail="reachable, but the provider lists no models")
        return out

    # --- stage 2: real completion with the configured model ---
    mdl = (model or "").strip() or ids[0]  # blank configured model → first listed
    payload = {"model": mdl,
               "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
               "max_tokens": 8, "temperature": 0}
    t1 = _time.monotonic()
    try:
        rc = requests.post(base + "/chat/completions", headers=headers,
                           json=payload, timeout=max(timeout_s, 30))
    except Exception as e:
        out.update(stage="chat", ok=False,
                   detail=f"/models OK ({len(ids)} model(s)), but chat/completions failed: "
                          f"{type(e).__name__}: {e}")
        return out
    out["latency_ms"] = int((_time.monotonic() - t1) * 1000)
    if rc.status_code != 200:
        body = ""
        try:
            body = rc.json().get("error", {}).get("message", "")
        except Exception:
            body = rc.text[:200] if rc.text else ""
        hint = {401: " — API key rejected", 404: f" — model {mdl} not offered"}.get(
            rc.status_code, "")
        out.update(stage="chat", ok=False,
                   detail=f"chat/completions → HTTP {rc.status_code}{hint}"
                          + (f": {body[:200]}" if body else ""))
        return out
    try:
        data = rc.json()
        text = (data["choices"][0]["message"] or {}).get("content") or ""
    except Exception:
        out.update(stage="chat", ok=False,
                   detail="chat/completions returned an unreadable response body")
        return out
    shown = ", ".join(ids[:3]) + (f" +{len(ids) - 3} more" if len(ids) > 3 else "")
    out.update(stage="done", ok=True,
               detail=f"chat OK via {mdl} ({out['latency_ms']} ms): {text.strip()[:80]!r}; "
                      f"{len(ids)} model(s): {shown}")
    return out
