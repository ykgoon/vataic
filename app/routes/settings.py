"""Settings panel: provider, budgets, thresholds, role models."""
from flask import Blueprint, request, render_template, redirect
from app.db import get_conn

bp = Blueprint("settings_view", __name__)


@bp.get("/settings")
def view():
    with get_conn() as c:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
    return render_template("settings/index.html",
        settings=rows,
        provider_name=rows.get("llm.provider", ""),
        model_name=rows.get("llm.model", ""),
        provider_status=rows.get("llm.status", "not configured"),
        llm_key_set="api_key" in rows or "llm_api_key" in rows or "llm" in rows,
        data_key_set="data_api_key" in rows or "data" in rows,
        version=rows.get("version", "unknown"),
        db_path=rows.get("database_path", "not set"))


@bp.post("/settings")
def save():
    k, v = request.form.get("key", ""), request.form.get("value", "")
    if k and "api_key" not in k and "secret" not in k:
        with get_conn() as c:
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, v))
    return redirect("/settings")
