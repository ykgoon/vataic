"""Futures view: outcome cards, fan chart, futures map, path stories,
cornerstone rationales (htmx swaps). Implements
openspec/changes/2026-10-25-futures-output (web-ui delta). The legacy
timeline tree stays as a debug route; the legacy card page remains as a
fallback for jobs without a futures view (non-completed)."""
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


def _scope_subtitle(scope: str) -> str:
    """Scope-aware phrasing for the fan-chart heading."""
    return "Where each future takes your factors" \
        if (scope or "macro") == "personal" \
        else "Where each future takes the key factors"


def _with_colors(fv: dict) -> dict:
    """Attach band colors to outcomes for template use (split bars, cards)."""
    from app.futures_svg import band_color
    for o in fv.get("outcomes", []):
        o["color"] = band_color(o.get("band"))
    return fv


@bp.get("/results/<jid>")
def results(jid):
    from app.db import get_conn
    from app.present import repair_legacy_outcomes
    with get_conn() as c:
        job = c.execute("SELECT id, premise, status FROM projection_jobs WHERE id=?",
                        (jid,)).fetchone()
    if not job:
        return "<p>missing job</p>", 404
    if job["status"] == "COMPLETED":
        # Legacy-outcome healing writes; skip for non-completed jobs so the
        # page never queues behind a busy worker's write lock.
        try:
            repair_legacy_outcomes(jid)
        except Exception:
            pass
    fv = None
    try:
        from app.futures import load_futures_view
        fv = load_futures_view(jid)
    except Exception:
        fv = None
    if fv and fv.get("outcomes"):
        from app.futures_svg import fan_svg, map_svg
        fv = _with_colors(fv)
        f0 = fv["factors"][0] if fv["factors"] else None
        fan_html, fan_legend = fan_svg(fv, f0["id"], f0["name"]) if f0 else ("", [])
        map_html, _cornerstones = map_svg(fv, jid)
        story_o = fv["outcomes"][0]
        expand_all = False
        try:
            from flask import request
            expand_all = request.args.get("expand") == "1"
        except Exception:
            pass
        return render_template(
            "results/index.html",
            job={"id": jid, "premise": job["premise"]},
            fv=fv,
            fan_html=fan_html,
            fan_legend=fan_legend,
            fan_factor=f0,
            map_html=map_html,
            story_o=story_o,
            scope_subtitle=_scope_subtitle(fv.get("scope", "macro")),
            expand_all=expand_all,
        )
    # Legacy fallback (job not completed / no artifact yet): old card view.
    from app.present import prepare_outcomes
    with get_conn() as c:
        outs = [dict(r) for r in c.execute("SELECT * FROM outcomes WHERE job_id=?", (jid,))]
        particles = [dict(r) for r in c.execute(
            "SELECT id,parent_id,probability FROM particles WHERE job_id=?", (jid,))]
        states = [dict(r) for r in c.execute(
            """SELECT ps.step_index, ps.factor_values FROM particle_states ps
               JOIN particles p ON p.id=ps.particle_id WHERE p.job_id=? LIMIT 50""", (jid,))]
    outcome_rows = prepare_outcomes(outs, jid)
    return render_template("results/legacy.html",
        job={"id": jid, "premise": job["premise"]},
        outcomes=outcome_rows,
        particles=particles,
        states=states)


def _wrap(inner: str, title: str = "Vataic") -> str:
    """Render a partial as a full page for direct (non-htmx) access,
    so deep links and screenshots get the site chrome."""
    from flask import render_template
    return render_template("results/partials_wrap.html", title=title, inner=inner)


def _is_htmx() -> bool:
    from flask import request
    return bool(request.headers.get("HX-Request"))


@bp.get("/results/<jid>/fan")
def fan(jid):
    """htmx partial: fan chart for one factor (tabs included)."""
    from app.db import get_conn
    from app.futures import load_futures_view
    from app.futures_svg import fan_svg
    from flask import request, render_template
    fv = load_futures_view(jid)
    if not fv:
        return "<p class='muted'>futures view unavailable</p>", 404
    factor = request.args.get("factor") or ""
    f = next((x for x in fv["factors"] if x["id"] == factor), None) \
        or (fv["factors"][0] if fv["factors"] else None)
    if not f:
        return "<p class='muted'>no factors recorded</p>"
    fan_html, legend = fan_svg(fv, f["id"], f["name"])
    rendered = render_template("results/partials/fan.html", jid=jid, fv=fv,
                               factor=f, fan_html=fan_html, legend=legend,
                               scope_subtitle=_scope_subtitle(fv.get("scope", "macro")))
    return rendered if _is_htmx() else _wrap(rendered, "Factor trajectories")


@bp.get("/results/<jid>/story")
def story(jid):
    """htmx partial: path story for one outcome, with cornerstone rationales."""
    from app.futures import load_futures_view
    from flask import request, render_template
    fv = load_futures_view(jid)
    if not fv:
        return "<p class='muted'>futures view unavailable</p>", 404
    oid = request.args.get("oid") or ""
    o = next((x for x in fv["outcomes"] if x["id"] == oid), None) \
        or (fv["outcomes"][0] if fv["outcomes"] else None)
    if not o:
        return "<p class='muted'>no outcomes</p>"
    _with_colors(fv)
    expand = request.args.get("expand") == "1"
    rendered = render_template("results/partials/story.html", jid=jid, o=o, fv=fv,
                               expand_all=expand)
    return rendered if _is_htmx() else _wrap(rendered, "Path story")


@bp.get("/results/<jid>/rationale")
def rationale(jid):
    """htmx partial: rationale for a branch-point cornerstone on the map."""
    from app.futures import load_futures_view
    from flask import request
    fv = load_futures_view(jid)
    if not fv:
        return "<p class='muted'>futures view unavailable</p>", 404
    try:
        step = int(request.args.get("bp") or -1)
    except Exception:
        step = -1
    bp = next((b for b in fv.get("branch_points", []) if int(b.get("step", -1)) == step), None)
    if not bp:
        rendered = "<p class='muted'>no fork recorded at this step</p>"
        return rendered if _is_htmx() else _wrap(rendered, "Fork rationale")
    _with_colors(fv)
    outs = {o["id"]: o for o in fv["outcomes"] if o["id"] in bp.get("outcomes", [])}
    rendered = render_template("results/partials/rationale.html", jid=jid, bp=bp,
                               outs=outs, fv=fv)
    return rendered if _is_htmx() else _wrap(rendered, "Fork rationale")


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
    """Debug view: raw particle lineage. Not linked from primary navigation."""
    from app.db import get_conn
    with get_conn() as c:
        parts = [dict(r) for r in c.execute(
            "SELECT id,parent_id,probability FROM particles WHERE job_id=?", (jid,))]
    items = "".join(
        f"<li><span hx-get='/results/{jid}/branch/{p['id']}' hx-target='#bdetail'>"
        f"{p['id']} ({p['probability']:.2f})</span></li>"
        for p in parts)
    return f"<h1>Timeline tree (debug)</h1><ol>{items}</ol><div id='bdetail'></div>"


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
