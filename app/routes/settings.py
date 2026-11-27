"""Settings page: view + edit runtime config, test the LLM provider.

Effective values = DB settings rows (the primary source) with env fallbacks
for keys that have no row. Edits write straight to the settings table; LLM
API keys and the Flask secret stay env-only and are never editable here.
"""
from flask import Blueprint, request, render_template, redirect, flash

bp = Blueprint("settings_view", __name__)


def _seed_defaults() -> None:
    try:
        from app.seed import seed
        seed()
    except Exception:
        pass


def _rows(db_path=None) -> dict:
    from app.db import get_conn
    with get_conn(db_path) as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}


def _effective(db_rows: dict) -> dict:
    """Effective config = env defaults, overridden by DB settings rows.

    A DB row that merely repeats the seeded default must not mask a real
    env-provided value: seed() writes env-independent defaults, so on a
    deployment configured purely via environment (the docker-compose path)
    the untouched default row would otherwise display and behave like the
    seed value instead of the operator's env.
    """
    from app.config import settings
    from app.seed import DEFAULTS
    eff = {
        "llm.provider": settings.llm_provider,
        "llm.base_url": settings.llm_base_url,
        "llm.primary_model": settings.llm_primary_model,
        "llm.strong_model": settings.llm_strong_model or settings.llm_primary_model,
        "llm.cheap_model": settings.llm_cheap_model or settings.llm_primary_model,
        "llm.timeout_s": settings.llm_timeout_s,
        "llm.max_attempts": settings.llm_max_attempts,
        "budget.max_steps": settings.budget_max_steps,
        "budget.max_runtime_s": settings.budget_max_runtime_s,
        "budget.max_branches": settings.budget_max_branches,
        "beam.width": settings.beam_width,
        "beam.reserved_divergent": settings.beam_reserved_divergent,
        "merge.distance_threshold": settings.merge_distance_threshold,
        "branch.prob_threshold": settings.branch_prob_threshold,
        "tail_risk.impact_threshold": settings.tail_risk_impact_threshold,
        "factors.max_macro": settings.factor_max_macro,
        "factors.max_personal": settings.factor_max_personal,
        "step.min_days": settings.step_min_days,
        "step.max_days": settings.step_max_days,
        "epoch.interval_steps": settings.epoch_interval_steps,
        "context.token_budget": settings.context_token_budget,
    }
    for k, v in db_rows.items():
        if k not in eff or v in (None, ""):
            continue
        if k in DEFAULTS and str(v) == str(DEFAULTS[k]) and str(eff[k]) != str(DEFAULTS[k]):
            continue  # untouched seed row loses to a real env value
        eff[k] = v
    return eff


def _db_path_display() -> str:
    from app.db import resolve_db_path
    return str(resolve_db_path())


@bp.get("/settings")
def view():
    from app.config import llm_api_key
    from app import settings_store
    _seed_defaults()
    eff = _effective(_rows())
    base_url = eff.get("llm.base_url", "")
    model_name = eff.get("llm.primary_model", "")

    # Status: fast reachability probe of the current effective config.
    probe = settings_store.probe_llm(base_url, llm_api_key(), model_name)
    status = ("reachable — " + probe["detail"]) if probe["ok"] else probe["detail"]

    def val(k):
        return str(eff.get(k, ""))

    return render_template("settings/index.html",
        settings_store=settings_store,
        eff=eff,
        val=val,
        provider_name=eff.get("llm.provider", ""),
        base_url=base_url,
        model_name=model_name,
        strong_model=eff.get("llm.strong_model", ""),
        cheap_model=eff.get("llm.cheap_model", ""),
        provider_status=status,
        llm_key_set=bool(llm_api_key()),
        version=_rows().get("version", "0.1.0"),
        db_path=_db_path_display())


@bp.post("/settings")
def save():
    """Multi-field save: editable keys arrive as form fields named by key."""
    from app import settings_store
    from app.config import refresh
    form = {k: v for k, v in request.form.items() if k in settings_store.ALLOWED}
    if not form:
        flash("No editable values were submitted.", "warning")
        return redirect("/settings")
    saved, errors = settings_store.save_many(form)
    refresh()
    for e in errors:
        flash(e, "danger")
    if saved:
        flash(f"Saved {len(saved)} setting(s).", "success")
    return redirect("/settings")


@bp.post("/settings/test-llm")
def test_llm():
    """Test the LLM provider with the values currently in the form (or the
    stored config when the test button is used outside an edit)."""
    from app.config import llm_api_key, refresh
    from app import settings_store

    # Form-provided values win so an unsaved edit can be tested first.
    base_url = request.form.get("llm.base_url", "").strip()
    model = request.form.get("llm.primary_model", "").strip()
    if not base_url:
        refresh()
        from app.config import settings
        base_url = settings.llm_base_url
        model = model or settings.llm_primary_model
    # Probe key: an inline override first, then the stored key (llm.api_key
    # row or env). The inline override is used for this request only.
    key = (request.form.get("llm.api_key_test", "").strip()
           or request.form.get("llm.api_key", "").strip()
           or llm_api_key())
    probe = settings_store.probe_llm(base_url, key, model)
    if probe["ok"]:
        flash(f"Provider OK — {probe['detail']}", "success")
    else:
        flash(f"Provider test failed ({probe['stage']}): {probe['detail']}", "danger")
    return redirect("/settings")
