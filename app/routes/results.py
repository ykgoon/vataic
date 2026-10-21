"""Results explorer: outcome cards, timeline tree, factor trajectories (htmx swaps)."""
from flask import Blueprint, render_template
import json
import html

bp = Blueprint("results", __name__)


def _split_lines(value, n=6):
    """Split a stored JSON array / newline-ish string into readable lines."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(x) for x in value[:n]]
    try:
        v = json.loads(value)
        if isinstance(v, list):
            return [str(x) for x in v][:n]
        if isinstance(v, dict):
            return [f"{k}: {v}" for k, v in list(v.items())[:n]]
    except Exception:
        pass
    # Fallback: split on newlines, commas, or semicolons
    import re
    parts = re.split(r"[\n;]|, ", str(value))
    return [p.strip() for p in parts if p.strip()][:n]


@bp.get("/results/<jid>")
def results(jid):
    from app.db import get_conn
    from app.present import (prepare_outcomes, repair_legacy_outcomes,
                             factor_names_for_job, pct)
    with get_conn() as c:
        outs = [dict(r) for r in c.execute("SELECT * FROM outcomes WHERE job_id=?", (jid,))]
        particles = [dict(r) for r in c.execute(
            "SELECT id,parent_id,probability FROM particles WHERE job_id=?", (jid,))]
        states = [dict(r) for r in c.execute(
            """SELECT ps.step_index, ps.factor_values FROM particle_states ps
               JOIN particles p ON p.id=ps.particle_id WHERE p.job_id=? LIMIT 50""", (jid,))]
        job = c.execute("SELECT premise FROM projection_jobs WHERE id=?", (jid,)).fetchone()
    try:
        repair_legacy_outcomes(jid)
        with get_conn() as c:
            outs = [dict(r) for r in c.execute("SELECT * FROM outcomes WHERE job_id=?", (jid,))]
    except Exception:
        pass
    outcome_rows = prepare_outcomes(outs, jid)
    return render_template("results/index.html",
        job={"id": jid, "premise": job["premise"] if job else ""},
        outcomes=outcome_rows,
        particles=particles,
        states=states)


@bp.get("/results/<jid>/outcome/<oid>")
def outcome_detail(jid, oid):
    from app.db import get_conn
    from app.present import prepare_outcomes, repair_legacy_outcomes, as_list
    repair_legacy_outcomes(jid)
    with get_conn() as c:
        o = c.execute("SELECT * FROM outcomes WHERE id=?", (oid,)).fetchone()
        alts = c.execute(
            "SELECT particle_id FROM outcome_particle_memberships WHERE outcome_id=?", (oid,)).fetchall()
    if not o:
        return "<p>missing</p>"

    row = prepare_outcomes([dict(o)], jid)[0]
    prob = row["probability"]
    band = html.escape(row["band"])
    name = html.escape(row["name"])
    summary = html.escape(row["summary"])
    indicators = row["indicators"]
    assumptions = row["assumptions"]
    risks = row["risks"]
    causal = row["causal_path"]
    explanation = html.escape(row["explanation"] or "No explanation recorded.")
    alts_list = [html.escape(a["particle_id"]) for a in alts]

    def ul(items, cls):
        if not items:
            return ""
        lis = "".join(f"<li>{html.escape(i)}</li>" for i in items)
        return f"<ul class='{cls}'>{lis}</ul>"

    parts = []
    parts.append(f"<h2 class='outcome-detail-name'>{name} <span class='outcome-prob'>{row['prob_display']}</span> [{band}]</h2>")
    parts.append(f"<p class='outcome-detail-summary'>{summary}</p>")
    parts.append(f"<details class='outcome-detail-explanation' open><summary>What this means</summary><p>{explanation}</p></details>")
    if causal:
        parts.append(f"<p class='outcome-detail-causal'><strong>How it unfolded:</strong></p>{ul(causal, 'causal-list')}")
    if indicators:
        parts.append(f"<p class='outcome-detail-indicators'><strong>Watch for (leading indicators):</strong></p>{ul(indicators, 'indicator-list')}")
    if assumptions:
        parts.append(f"<p class='outcome-detail-assumptions'><strong>Assumes:</strong></p>{ul(assumptions, 'assumption-list')}")
    if risks:
        parts.append(f"<p class='outcome-detail-risks'><strong>What could break it:</strong></p>{ul(risks, 'risk-list')}")
    if row["movers"]:
        fac = "".join(f"<li>{html.escape(n)}: {v:.0f}/100</li>" for n, v in row["movers"])
        parts.append(f"<p class='outcome-detail-factors'><strong>Key factors (0=low, 100=high):</strong></p><ul class='factor-list'>{fac}</ul>")
    if alts_list:
        parts.append(f"<p class='outcome-detail-alts'><strong>Alternative timelines in this cluster:</strong> {', '.join(alts_list)}</p>")
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
    from app.present import factor_names_for_job
    names = factor_names_for_job(jid)
    with get_conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT ps.step_index, ps.factor_values FROM particle_states ps
               JOIN particles p ON p.id=ps.particle_id WHERE p.job_id=? LIMIT 50""", (jid,))]
    # One polyline per factor that ever moves, labeled with human names.
    series: dict[str, list[tuple[int, float]]] = {}
    for r in rows:
        try:
            fv = json.loads(r["factor_values"])
        except Exception:
            continue
        for k, v in fv.items():
            try:
                series.setdefault(k, []).append((int(r["step_index"]), float(v)))
            except Exception:
                continue
    moving = {k: v for k, v in series.items()
              if any(abs(val - 50.0) >= 3.0 for _, val in v)}
    if not moving and series:
        moving = dict(list(series.items())[:4])
    colors = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0891b2"]
    w, h = 640, 240
    lines = []
    legend = []
    for i, (k, pts) in enumerate(sorted(moving.items())[:6]):
        pts = sorted(pts)
        n = max(len(pts) - 1, 1)
        poly = " ".join(f"{60 + (s / n) * (w - 80):.0f},{h - 30 - (v / 100) * (h - 60):.0f}"
                        for s, v in pts)
        color = colors[i % len(colors)]
        lines.append(f"<polyline points='{poly}' fill='none' stroke='{color}' stroke-width='2'/>")
        legend.append(f"<span style='color:{color};font-weight:600'>●</span> {html.escape(names.get(k, k))}")
    legend_html = " &nbsp; ".join(legend) if legend else "<span class='muted'>no factor movement recorded</span>"
    return (f"<h1>Factor trajectories</h1>"
            f"<p class='muted'>Factor levels (0–100) across projection steps. Baseline is 50.</p>"
            f"<svg width={w} height={h} style='background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px'>"
            f"{''.join(lines)}</svg>"
            f"<p>{legend_html}</p>")