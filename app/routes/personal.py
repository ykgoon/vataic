"""Personal builder, privacy controls, export/delete, safety interstitial."""
from flask import Blueprint, request, render_template, redirect
import json

bp = Blueprint("personal", __name__)


@bp.get("/personal")
def personal_index():
    from app.scenarios.repos import list_scenarios
    scenarios = [j for j in list_scenarios() if j.get("scope") == "personal"]
    return render_template("personal/index.html", personal_scenarios=scenarios)


@bp.get("/personal/<jid>")
def builder(jid):
    from app.steps.personal import get_subject
    from app.db import get_conn
    s = get_subject(jid)
    with get_conn() as c:
        flag = c.execute(
            "SELECT * FROM safety_flags WHERE scenario_id=? ORDER BY id DESC LIMIT 1", (jid,)).fetchone()
    if flag and flag["flagged"]:
        return render_template("personal/safety.html", flag=flag)
    return render_template("personal/builder.html", scenario={"id": jid}, subject=s, flag=flag)


@bp.post("/personal/<jid>")
def save(jid):
    from app.steps.personal import create_subject
    try:
        profile = json.loads(request.form.get("profile", "{}"))
    except Exception:
        profile = {}
    sid = create_subject(jid, profile)
    return redirect(f"/personal/{jid}")


@bp.post("/personal/<jid>/export")
def export(jid):
    from app.store import write_artifact
    from app.db import get_conn
    with get_conn() as c:
        data = {
            "scenario": jid,
            "subjects": [dict(r) for r in c.execute(
                "SELECT * FROM subjects WHERE scenario_id=?", (jid,))],
            "outcomes": [dict(r) for r in c.execute(
                "SELECT * FROM outcomes WHERE scenario_id=?", (jid,))]
        }
    art = write_artifact(jid, "export", json.loads(json.dumps(data, default=str)))
    return redirect(f"/scenarios/{jid}")


@bp.post("/personal/<jid>/delete")
def delete(jid):
    from app.steps.personal import delete_personal
    delete_personal(jid)
    return redirect(f"/personal/{jid}")
