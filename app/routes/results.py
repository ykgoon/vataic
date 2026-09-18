"""Results explorer: outcome cards, timeline tree, factor trajectories (htmx swaps)."""
from flask import Blueprint
import json

bp = Blueprint("results", __name__)

@bp.get("/results/<jid>")
def results(jid):
    from app.db import get_conn
    with get_conn() as c:
        outs = [dict(r) for r in c.execute("SELECT * FROM outcomes WHERE job_id=?", (jid,))]
    cards = "".join(
        f"<div hx-get='/results/{jid}/outcome/{o['id']}' hx-trigger='click' hx-target='#detail'>"
        f"<b>{o['name']}</b> {o['probability']:.0%} [{o['band']}]<br>{o['summary'][:200]}</div><hr>"
        for o in outs) or "<p>No outcomes yet.</p>"
    return (f"<h1>Outcomes {jid}</h1><div>{cards}</div><div id='detail'></div>"
            f"<div><a href='/results/{jid}/tree'>timeline tree</a> · "
            f"<a href='/results/{jid}/trajectories'>trajectories</a></div>"
            f"<p><i>Structured scenario explorations, not predictions.</i></p>"
            f"<script src='/static/htmx.min.js'></script>")

@bp.get("/results/<jid>/outcome/<oid>")
def outcome_detail(jid, oid):
    from app.db import get_conn
    with get_conn() as c:
        o = c.execute("SELECT * FROM outcomes WHERE id=?", (oid,)).fetchone()
        alts = c.execute("SELECT particle_id FROM outcome_particle_memberships WHERE outcome_id=?", (oid,)).fetchall()
    if not o: return "<p>missing</p>"
    return (f"<h2>{o['name']} {o['probability']:.0%} [{o['band']}]</h2><p>{o['explanation']}</p>"
            f"<p>causal path: {o['causal_path'][:1000]}</p><p>indicators: {o['indicators'][:500]}</p>"
            f"<p>alternatives: {', '.join(a['particle_id'] for a in alts)}</p>")

@bp.get("/results/<jid>/tree")
def tree(jid):
    from app.db import get_conn
    with get_conn() as c:
        parts = [dict(r) for r in c.execute("SELECT id,parent_id,probability FROM particles WHERE job_id=?", (jid,))]
    items = "".join(
        f"<li><span hx-get='/results/{jid}/branch/{p['id']}' hx-target='#bdetail'>{p['id']} ({p['probability']:.2f})</span></li>"
        for p in parts)
    return f"<h1>Timeline tree</h1><ol>{items}</ol><div id='bdetail'></div><script src='/static/htmx.min.js'></script>"

@bp.get("/results/<jid>/branch/<pid>")
def branch(jid, pid):
    from app.db import get_conn
    with get_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM particle_states WHERE particle_id=? ORDER BY step_index", (pid,))]
    return f"<pre>{json.dumps(rows)[:3000]}</pre>"

@bp.get("/results/<jid>/trajectories")
def traj(jid):
    from app.db import get_conn
    with get_conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT ps.step_index, ps.factor_values FROM particle_states ps
               JOIN particles p ON p.id=ps.particle_id WHERE p.job_id=? LIMIT 50""", (jid,))]
    # inline SVG sparkline of first factor
    pts = []
    for i, r in enumerate(rows):
        try: v = list(json.loads(r["factor_values"]).values())[0]
        except Exception: v = 50
        pts.append(f"{i*20},{100-float(v)}")
    poly = " ".join(pts)
    return (f"<h1>Factor trajectories</h1><svg width=600 height=120><polyline points='{poly}' "
            f"fill='none' stroke='blue'/></svg><script src='/static/htmx.min.js'></script>")
