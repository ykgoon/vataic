"""Personal builder, privacy controls, export/delete, safety interstitial."""
from flask import Blueprint, request, jsonify
import json

bp = Blueprint("personal", __name__)

@bp.get("/personal/<jid>")
def builder(jid):
    from app.steps.personal import get_subject
    from app.db import get_conn
    s = get_subject(jid)
    with get_conn() as c:
        flag = c.execute("SELECT * FROM safety_flags WHERE job_id=? ORDER BY id DESC LIMIT 1", (jid,)).fetchone()
    if flag and flag["flagged"]:
        return (f"<h1>Safety pause</h1><p>{flag['reason']}</p><p>{flag['resources']}</p>"
                f"<p>No projection generated. Please seek professional/emergency support.</p>")
    return (f"<h1>Personal builder {jid}</h1><pre>{json.dumps(s, default=str)[:2000] if s else 'no subject'}</pre>"
            f"<form method=post action='/personal/{jid}'><textarea name=profile rows=6 cols=60>"
            f"{{\"goals\":[]}}</textarea><button>save</button></form>"
            f"<form method=post action='/personal/{jid}/export'><button>export</button></form>"
            f"<form method=post action='/personal/{jid}/delete'><button>delete personal data</button></form>")

@bp.post("/personal/<jid>")
def save(jid):
    from app.steps.personal import create_subject
    try: profile = json.loads(request.form.get("profile", "{}"))
    except Exception: profile = {}
    sid = create_subject(jid, profile)
    return f"<p>saved {sid} (private by default)</p>"

@bp.post("/personal/<jid>/export")
def export(jid):
    from app.store import write_artifact
    from app.db import get_conn
    with get_conn() as c:
        data = {"job": jid,
                "subjects": [dict(r) for r in c.execute("SELECT * FROM subjects WHERE job_id=?", (jid,))],
                "outcomes": [dict(r) for r in c.execute("SELECT * FROM outcomes WHERE job_id=?", (jid,))]}
    art = write_artifact(jid, "export", json.loads(json.dumps(data, default=str)))
    return f"<a href='#'>{art['path']}</a>"

@bp.post("/personal/<jid>/delete")
def delete(jid):
    from app.steps.personal import delete_personal
    delete_personal(jid)
    return "<p>personal data deleted</p>"
