"""Results explorer: outcome cards, timeline tree, factor trajectories (htmx swaps)."""
from flask import Blueprint, render_template
import json
import html

bp = Blueprint("results", __name__)


def _split_lines(value, n=5):
    """Split a JSON-ish string into lines (handles JSON arrays or newline-delimited)."""
    if not value:
        return []
    try:
        v = json.loads(value)
        if isinstance(v, list):
            return [str(x) for x in v][:n]
    except Exception:
        pass
    # Fallback: split on newlines, commas, or semicolons
    import re
    parts = re.split(r"[\n;]|, ", str(value))
    return [p.strip() for p in parts if p.strip()][:n]


@bp.get("/results/<jid>")
def results(jid):
    from app.db import get_conn
    with get_conn() as c:
        outs = [dict(r) for r in c.execute("SELECT * FROM outcomes WHERE job_id=?", (jid,))]
        particles = [dict(r) for r in c.execute(
            "SELECT id,parent_id,probability FROM particles WHERE job_id=?", (jid,))]
        states = [dict(r) for r in c.execute(
            """SELECT ps.step_index, ps.factor_values FROM particle_states ps
               JOIN particles p ON p.id=ps.particle_id WHERE p.job_id=? LIMIT 50""", (jid,))]
    return render_template("results/index.html",
        job={"id": jid},
        outcomes=outs,
        particles=particles,
        states=states)


@bp.get("/results/<jid>/outcome/<oid>")
def outcome_detail(jid, oid):
    from app.db import get_conn
    with get_conn() as c:
        o = c.execute("SELECT * FROM outcomes WHERE id=?", (oid,)).fetchone()
        alts = c.execute(
            "SELECT particle_id FROM outcome_particle_memberships WHERE outcome_id=?", (oid,)).fetchall()
    if not o:
        return "<p>missing</p>"

    name = html.escape(o["name"] or "Untitled")
    prob = o["probability"]
    band = html.escape(o["band"] or "unknown")
    summary = html.escape(o["summary"] or "")
    causal = html.escape(o["causal_path"] or "n/a")
    indicators = _split_lines(o["indicators"])
    assumptions = _split_lines(o["assumptions"])
    risks = _split_lines(o["risks"])
    explanation = html.escape(o["explanation"] or "No explanation recorded.")
    factors = _split_lines(o["factor_states"])
    alts_list = [html.escape(a["particle_id"]) for a in alts]

    def ul(items, cls):
        if not items:
            return ""
        lis = "".join(f"<li>{i}</li>" for i in items)
        return f"<ul class='{cls}'>{lis}</ul>"

    parts = []
    parts.append(f"<h2 class='outcome-detail-name'>{name} <span class='outcome-prob'>{prob:.0%}</span> [{band}]</h2>")
    parts.append(f"<p class='outcome-detail-summary'>{summary}</p>")
    parts.append(f"<p class='outcome-detail-causal'><strong>Causal path:</strong> {causal}</p>")
    if indicators:
        parts.append(f"<p class='outcome-detail-indicators'><strong>Leading indicators:</strong> {ul(indicators, 'indicator-list')}</p>")
    if assumptions:
        parts.append(f"<p class='outcome-detail-assumptions'><strong>Assumptions:</strong> {ul(assumptions, 'assumption-list')}</p>")
    if risks:
        parts.append(f"<p class='outcome-detail-risks'><strong>Risks:</strong> {ul(risks, 'risk-list')}</p>")
    if explanation:
        parts.append(f"<details class='outcome-detail-explanation'><summary>Expandable explanation</summary><p>{explanation}</p></details>")
    if alts_list:
        parts.append(f"<p class='outcome-detail-alts'><strong>Alternative particles:</strong> {', '.join(alts_list)}</p>")
    if factors:
        parts.append(f"<p class='outcome-detail-factors'><strong>Key factors:</strong> {', '.join(factors)}</p>")
    return "".join(parts)


@bp.get("/results/<jid>/tree")
def tree(jid):
    from app.db import get_conn
    with get_conn() as c:
        parts = [dict(r) for r in c.execute(
            "SELECT id,parent_id,probability FROM particles WHERE job_id=?", (jid,))]
    items = "".join(
        f"<li><span hx-get='/results/{jid}/branch/{p['id']}' hx-target='#bdetail'>"
        f"{p['id']} ({p['probability']:.2f})</span></li>"
        for p in parts)
    return f"<h1>Timeline tree</h1><ol>{items}</ol><div id='bdetail'></div>"


@bp.get("/results/<jid>/branch/<pid>")
def branch(jid, pid):
    from app.db import get_conn
    with get_conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM particle_states WHERE particle_id=? ORDER BY step_index", (pid,))]
    return f"<pre>{json.dumps(rows)[:3000]}</pre>"


@bp.get("/results/<jid>/trajectories")
def traj(jid):
    from app.db import get_conn
    with get_conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT ps.step_index, ps.factor_values FROM particle_states ps
               JOIN particles p ON p.id=ps.particle_id WHERE p.job_id=? LIMIT 50""", (jid,))]
    pts = []
    for i, r in enumerate(rows):
        try:
            v = list(json.loads(r["factor_values"]).values())[0]
        except Exception:
            v = 50
        pts.append(f"{i*20},{100-float(v)}")
    poly = " ".join(pts)
    return (f"<h1>Factor trajectories</h1>"
            f"<svg width=600 height=120><polyline points='{poly}' fill='none' stroke='blue'/></svg>")