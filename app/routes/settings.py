"""Settings panel: provider, budgets, thresholds, role models."""
from flask import Blueprint, request
from app.db import get_conn

bp = Blueprint("settings_view", __name__)

@bp.get("/settings")
def view():
    with get_conn() as c:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
    items = "".join(f"<li>{k} = {v}</li>" for k, v in sorted(rows.items()))
    return (f"<h1>Settings</h1><ul>{items}</ul>"
            f"<form method=post>key <input name=key> value <input name=value>"
            f"<button>save</button></form><p>API key comes from server env only, never stored.</p>")

@bp.post("/settings")
def save():
    k, v = request.form.get("key", ""), request.form.get("value", "")
    if k and "api_key" not in k and "secret" not in k:
        with get_conn() as c:
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, v))
    return "<p>saved (applies to subsequent jobs)</p>"
