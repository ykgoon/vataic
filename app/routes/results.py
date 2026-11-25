"""Futures view: outcome cards, fan chart, futures map, path stories,
cornerstone rationales (htmx swaps). Implements
openspec/changes/2026-10-25-futures-output (web-ui delta). The fan chart
covers the factor timelines; the legacy card page remains as a fallback for
scenarios without a futures view (non-completed)."""
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
        scenario = c.execute("SELECT id, premise, status FROM projection_scenarios WHERE id=?",
                        (jid,)).fetchone()
    if not scenario:
        return "<p>missing scenario</p>", 404
    if scenario["status"] == "COMPLETED":
        # Legacy-outcome healing writes; skip for non-completed scenarios so the
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
            scenario={"id": jid, "premise": scenario["premise"]},
            fv=fv,
            fan_html=fan_html,
            fan_legend=fan_legend,
            fan_factor=f0,
            map_html=map_html,
            story_o=story_o,
            scope_subtitle=_scope_subtitle(fv.get("scope", "macro")),
            expand_all=expand_all,
        )
    # Legacy fallback (scenario not completed / no artifact yet): old card view.
    from app.present import prepare_outcomes
    with get_conn() as c:
        outs = [dict(r) for r in c.execute("SELECT * FROM outcomes WHERE scenario_id=?", (jid,))]
        particles = [dict(r) for r in c.execute(
            "SELECT id,parent_id,probability FROM particles WHERE scenario_id=?", (jid,))]
        states = [dict(r) for r in c.execute(
            """SELECT ps.step_index, ps.factor_values FROM particle_states ps
               JOIN particles p ON p.id=ps.particle_id WHERE p.scenario_id=? LIMIT 50""", (jid,))]
    outcome_rows = prepare_outcomes(outs, jid)
    return render_template("results/legacy.html",
        scenario={"id": jid, "premise": scenario["premise"]},
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
    return rendered if _is_htmx() else _wrap(rendered, "Factor timeline")


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
    hide_explanation = request.args.get("hide_explanation") == "1"
    rendered = render_template("results/partials/story.html", jid=jid, o=o, fv=fv,
                               expand_all=expand, hide_explanation=hide_explanation)
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
    from app.present import prepare_outcomes, repair_legacy_outcomes
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
    # One consolidated narrative list: each factor appears once, with what to
    # watch and what would undo the path. The old causal/indicator/assumption/
    # risk lists each restated the same ranked factors.
    with get_conn() as c:
        mrow = c.execute("SELECT factor_states FROM outcomes WHERE id=?", (oid,)).fetchone()
    try:
        fs = json.loads((mrow["factor_states"] if mrow else "") or "{}")
    except Exception:
        fs = {}
    from app.present import factor_names_for_scenario, watches_for
    names = factor_names_for_scenario(jid)
    watches = watches_for(fs, names)
    if watches:
        parts.append(f"<p class='outcome-detail-watches'><strong>What to watch, and what would undo this path:</strong></p>{ul(watches, 'watch-list')}")
    if alts_list:
        parts.append(f"<p class='outcome-detail-alts'><strong>Alternative timelines in this cluster:</strong> {', '.join(alts_list)}</p>")
    return "".join(parts)
