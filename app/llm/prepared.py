"""LLM provider preflight: run-readiness checks for scenario execution.

A no-LLM run produces only deterministic template prose, which reads like
analysis but is not one (decision 2026-11-01): scenarios must fail fast at
worker pickup with a clear, retryable error instead of silently degrading.
VATAIC_ALLOW_HEURISTIC=1 restores the fallback behavior (tests, emergencies).
"""
import os

import requests


def provider_status(db_path=None) -> dict:
    """Effective-config provider probe.

    Returns {"ok": bool, "detail": str, "model": str}.

    - Connection/timeout errors -> not ok; the detail carries the reason.
    - Reachable with a model list -> ok only when the configured primary and
      strong models are offered.
    - Reachable without a listable catalog (common for OpenAI-compatible
      proxies) -> ok, noted in the detail.
    """
    from app.config import settings
    from app.llm.client import model_for_phase
    base = settings.llm_base_url.rstrip("/")
    model = settings.llm_primary_model
    strong = model_for_phase("chat")
    if not base:
        return {"ok": False, "detail": "no LLM base URL configured", "model": model}
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
    try:
        r = requests.get(base + "/models", headers=headers, timeout=3)
    except Exception as e:
        return {"ok": False, "detail": f"unreachable: {type(e).__name__}: {e}", "model": model}
    if r.status_code == 404:
        return {"ok": True, "detail": "reachable (no model list exposed)", "model": model}
    if r.status_code != 200:
        return {"ok": True, "detail": f"reachable (HTTP {r.status_code} on /models)", "model": model}
    try:
        ids = [str(m.get("id", "")) for m in (r.json().get("data") or [])]
    except Exception:
        return {"ok": True, "detail": "reachable (model list unreadable)", "model": model}
    if not ids:
        return {"ok": True, "detail": "reachable (no models listed)", "model": model}
    missing = sorted({m for m in (model, strong) if m and m not in ids})
    if missing:
        shown = ", ".join(ids[:3]) + (f" +{len(ids) - 3} more" if len(ids) > 3 else "")
        return {"ok": False,
                "detail": f"model(s) not offered: {', '.join(missing)}; provider offers: {shown}",
                "model": model}
    return {"ok": True, "detail": f"reachable — {len(ids)} model(s) including configured", "model": model}


def allow_heuristic() -> bool:
    return os.environ.get("VATAIC_ALLOW_HEURISTIC", "") in ("1", "true", "yes")


def require_provider(scenario_id: str, db_path=None) -> None:
    """Gate at pipeline pickup.

    Raises RuntimeError when the provider is not ready and the heuristic
    escape hatch is unset. run_sync turns the raise into status FAILED with
    this message, which the UI already surfaces with a retry control.
    """
    if allow_heuristic():
        return
    st = provider_status(db_path)
    if not st["ok"]:
        raise RuntimeError(f"LLM provider unavailable: {st['detail']}")
