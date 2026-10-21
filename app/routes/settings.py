"""Settings panel: provider, budgets, thresholds, role models.

Values shown are the *effective* runtime configuration: env-backed
(app.config.settings) merged with DB-stored overrides (settings table).
The DB table is auto-seeded with defaults on first view, so the page never
shows "not configured" for values that actually exist.
"""
from flask import Blueprint, request, render_template, redirect

bp = Blueprint("settings_view", __name__)

_NUM_KEYS = {
    "budget.max_steps", "budget.max_llm_calls", "budget.max_tokens",
    "budget.max_runtime_s", "budget.max_branches",
    "beam.width", "beam.reserved_divergent",
    "merge.distance_threshold", "factors.max_macro", "factors.max_personal",
    "step.min_days", "step.max_days",
}


def _seed_defaults() -> None:
    try:
        from app.seed import seed
        seed()
    except Exception:
        pass


def _effective(db_rows: dict) -> dict:
    """Effective config = env defaults, overridden by DB settings rows."""
    from app.config import settings
    eff = {
        "llm.provider": settings.llm_provider,
        "llm.base_url": settings.llm_base_url,
        "llm.primary_model": settings.llm_primary_model,
        "llm.strong_model": settings.llm_strong_model or settings.llm_primary_model,
        "llm.cheap_model": settings.llm_cheap_model or settings.llm_primary_model,
        "budget.max_steps": settings.budget_max_steps,
        "budget.max_llm_calls": settings.budget_max_llm_calls,
        "budget.max_tokens": settings.budget_max_tokens,
        "budget.max_runtime_s": settings.budget_max_runtime_s,
        "budget.max_branches": settings.budget_max_branches,
        "beam.width": settings.beam_width,
        "beam.reserved_divergent": settings.beam_reserved_divergent,
        "merge.distance_threshold": settings.merge_distance_threshold,
        "factors.max_macro": settings.factor_max_macro,
        "factors.max_personal": settings.factor_max_personal,
        "step.min_days": settings.step_min_days,
        "step.max_days": settings.step_max_days,
    }
    for k, v in db_rows.items():
        if k in eff and v not in (None, ""):
            eff[k] = v
    return eff


def _db_path_display() -> str:
    from app.db import resolve_db_path
    return str(resolve_db_path())


def _llm_reachable() -> tuple[bool, str]:
    """Cheap reachability probe of the configured base URL (no key sent)."""
    import requests
    from app.config import settings
    try:
        r = requests.get(settings.llm_base_url.rsplit("/v1", 1)[0] + "/",
                         timeout=3)
        return True, f"reachable (HTTP {r.status_code})"
    except Exception as e:
        return False, f"unreachable: {type(e).__name__}"


@bp.get("/settings")
def view():
    from app.db import get_conn
    _seed_defaults()
    with get_conn() as c:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
    eff = _effective(rows)

    def val(k):
        v = eff.get(k, "")
        if k in _NUM_KEYS:
            try:
                return f"{int(float(v)):,}" if float(v) == int(float(v)) else str(v)
            except Exception:
                return str(v)
        return str(v or "")

    from app.config import settings
    ok, reach = _llm_reachable()
    provider_ok = ok and bool(settings.llm_base_url)

    return render_template("settings/index.html",
        settings=rows,
        eff=eff,
        val=val,
        provider_name=eff.get("llm.provider", ""),
        base_url=eff.get("llm.base_url", ""),
        model_name=eff.get("llm.primary_model", ""),
        strong_model=eff.get("llm.strong_model", ""),
        cheap_model=eff.get("llm.cheap_model", ""),
        provider_status=("configured — " + reach) if provider_ok else ("not reachable — " + reach),
        llm_key_set=bool(settings.llm_api_key),
        data_key_set=False,  # no separate data-provider key in this version
        version=rows.get("version", "0.1.0"),
        db_path=_db_path_display())


@bp.post("/settings")
def save():
    k, v = request.form.get("key", ""), request.form.get("value", "")
    if k and "api_key" not in k and "secret" not in k:
        with get_conn() as c:
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, v))
    return redirect("/settings")
