"""Job routes: submission form, dashboard with htmx polling, detail + controls."""
import json
from flask import Blueprint, request, redirect, url_for, render_template_string

bp = Blueprint("jobs", __name__)

FORM = """<h1>New scenario</h1>
<form method=post action="/jobs/new">
<textarea name=premise rows=4 cols=60 placeholder="What if..."></textarea><br>
Scope <select name=scope><option>macro</option><option>meso</option><option>personal</option><option>hybrid</option></select>
Mode <select name=reality_mode><option>grounded</option><option>speculative</option><option>fantastical</option></select>
Horizon <input name=horizon value="auto">
<button>Submit</button></form>
<script src="/static/htmx.min.js"></script>"""

@bp.get("/jobs/new")
def new_form(): return FORM

@bp.post("/jobs/new")
def new_job():
    from app.jobs.repositories import create_job
    jid = create_job(request.form.get("premise", ""), request.form.get("scope", "macro"),
                     request.form.get("reality_mode", "grounded"), request.form.get("horizon", "auto"))
    return (f"<p>Job accepted: {jid}. Projection may take hours; you may close the browser.</p>"
            f"<a href='/jobs/{jid}'>track job</a>")

@bp.get("/jobs")
def dashboard():
    from app.jobs.repositories import list_jobs
    rows = "".join(
        f"<tr><td><a href='/jobs/{j['id']}'>{j['id']}</a></td><td>{j['premise'][:60]}</td>"
        f"<td>{j['status']}</td><td>{j['current_step']}</td>"
        f"<td>{'FAILED: '+str(j['error']) if j['status']=='FAILED' else ''}"
        f"{' <button hx-post=/jobs/'+j['id']+'/retry>retry</button>' if j['status']=='FAILED' else ''}</td></tr>"
        for j in list_jobs())
    return (f"<h1>Jobs</h1><table hx-get='/jobs/partial' hx-trigger='every 5s' hx-swap='outerHTML'>"
            f"{rows}</table><script src='/static/htmx.min.js'></script>")

@bp.get("/jobs/partial")
def dashboard_partial():
    from app.jobs.repositories import list_jobs
    rows = "".join(f"<tr><td>{j['id']}</td><td>{j['status']}</td><td>{j['current_step']}</td></tr>"
                   for j in list_jobs())
    return f"<table hx-get='/jobs/partial' hx-trigger='every 5s' hx-swap='outerHTML'>{rows}</table>"

@bp.get("/jobs/<jid>")
def detail(jid):
    from app.jobs.repositories import get_job, latest_checkpoint
    j = get_job(jid) or {}
    cp = latest_checkpoint(jid) or {}
    b = json.loads(j.get("budget_state", "{}")) if j.get("budget_state") else {}
    return (f"<h1>Job {jid}</h1><p>phase={j.get('current_phase')} step={j.get('current_step')} "
            f"status={j.get('status')}</p><p>LLM calls={b.get('llm_calls',0)} tokens={b.get('tokens',0)}</p>"
            f"<button hx-post='/jobs/{jid}/cancel'>cancel</button>"
            f"<button hx-post='/jobs/{jid}/suspend'>suspend</button>"
            f"<button hx-post='/jobs/{jid}/resume'>resume</button>"
            f"<button hx-post='/jobs/{jid}/retry'>retry</button>"
            f"<div>checkpoint: {json.dumps(cp.get('payload', {}))[:500]}</div>"
            f"<a href='/jobs/{jid}/review'>review assumptions</a> · <a href='/results/{jid}'>results</a>"
            f"<script src='/static/htmx.min.js'></script>")

def _ctrl(jid, action):
    from app.jobs.repositories import get_job, transition, append_event
    from app.db import get_conn
    with get_conn() as c:
        if action == "cancel":
            c.execute("UPDATE projection_jobs SET cancel_requested=1 WHERE id=?", (jid,))
        elif action == "suspend":
            c.execute("UPDATE projection_jobs SET suspend_requested=1 WHERE id=?", (jid,))
        elif action in ("resume", "retry"):
            c.execute("UPDATE projection_jobs SET cancel_requested=0, suspend_requested=0 WHERE id=?", (jid,))
    j = get_job(jid) or {}
    st = j.get("status", "DRAFT")
    try:
        if action == "cancel" and st != "CANCELLED": transition(jid, "CANCELLED", "user cancel")
        elif action == "suspend" and st in ("RUNNING", "CHECKPOINTING"): transition(jid, "SUSPENDED", "user suspend")
        elif action == "resume" and st == "SUSPENDED": transition(jid, "RUNNING", "user resume")
        elif action == "retry" and st in ("FAILED", "CANCELLED"): transition(jid, "QUEUED", "user retry")
    except Exception as e:
        return f"<span>error: {e}</span>"
    return f"<span>{action} ok</span>"

@bp.post("/jobs/<jid>/cancel")
def cancel(jid): return _ctrl(jid, "cancel")
@bp.post("/jobs/<jid>/suspend")
def suspend(jid): return _ctrl(jid, "suspend")
@bp.post("/jobs/<jid>/resume")
def resume(jid): return _ctrl(jid, "resume")
@bp.post("/jobs/<jid>/retry")
def retry(jid): return _ctrl(jid, "retry")

@bp.get("/jobs/<jid>/review")
def review(jid):
    from app.db import get_conn
    with get_conn() as c:
        s = c.execute("SELECT * FROM scenario_inputs WHERE job_id=?", (jid,)).fetchone()
        f = c.execute("SELECT * FROM factor_sets WHERE job_id=? ORDER BY id DESC LIMIT 1", (jid,)).fetchone()
        g = c.execute("SELECT * FROM causal_graphs WHERE job_id=? ORDER BY id DESC LIMIT 1", (jid,)).fetchone()
    return (f"<h1>Review {jid}</h1><h2>Assumptions</h2><pre>{s['assumptions'] if s else '[]'}</pre>"
            f"<h2>Factors</h2><pre>{(f['factors'] if f else '[]')[:2000]}</pre>"
            f"<h2>Watchlist</h2><pre>{f['watchlist'] if f else '[]'}</pre>"
            f"<h2>Causal graph</h2><pre>{(g['edges'] if g else '[]')[:2000]}</pre>"
            f"<form method=post action='/jobs/{jid}/review'><textarea name=factors rows=6 cols=60>"
            f"{f['factors'] if f else '[]'}</textarea><button>save before loop</button></form>")

@bp.post("/jobs/<jid>/review")
def review_save(jid):
    from app.db import get_conn
    fac = request.form.get("factors", "[]")
    with get_conn() as c:
        c.execute("INSERT INTO factor_sets(job_id,factors) VALUES(?,?)", (jid, fac))
    return redirect(f"/jobs/{jid}")
