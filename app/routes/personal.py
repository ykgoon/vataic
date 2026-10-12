"""Personal builder, privacy controls, export/delete, safety interstitial."""
from flask import Blueprint, request, render_template, redirect
import json

bp = Blueprint("personal", __name__)


@bp.get("/personal")
def personal_index():
    from app.jobs.repositories import list_jobs
    jobs = [j for j in list_jobs() if j.get("scope") == "personal"]
    return render_template("personal/index.html", personal_jobs=jobs)


@bp.get("/personal/<jid>")
def builder(jid):
    from app.steps.personal import get_subject
    from app.db import get_conn
    s = get_subject(jid)
    with get_conn() as c:
        flag = c.execute(
            "SELECT * FROM safety_flags WHERE job_id=? ORDER BY id DESC LIMIT 1", (jid,)).fetchone()
    if flag and flag["flagged"]:
        return render_template("personal/safety.html", flag=flag)
    return render_template("personal/builder.html", job={"id": jid}, subject=s, flag=flag)


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
            "job": jid,
            "subjects": [dict(r) for r in c.execute(
                "SELECT * FROM subjects WHERE job_id=?", (jid,))],
            "outcomes": [dict(r) for r in c.execute(
                "SELECT * FROM outcomes WHERE job_id=?", (jid,))]
        }
    art = write_artifact(jid, "export", json.loads(json.dumps(data, default=str)))
    return redirect(f"/jobs/{jid}")


@bp.post("/personal/<jid>/delete")
def delete(jid):
    from app.steps.personal import delete_personal
    delete_personal(jid)
    return redirect(f"/personal/{jid}")
