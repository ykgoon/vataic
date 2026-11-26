"""Futures view partials: fan chart, futures map, path stories, and
cornerstone rationales, all served as htmx fragments for the scenario page.

The full result page that used to live here was merged into
/scenarios/<jid>; /results/<jid> now redirects there. The fragments below are
still addressed under /results/ so existing hx-get attributes keep working.
"""
from flask import Blueprint

bp = Blueprint("results", __name__)


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
    """Superseded: a scenario's results now live on its own page.

    This route used to render a second full page for the same scenario, so a
    completed run was a status page and an answer page with a "View Results"
    button between them. /scenarios/<jid> renders both halves now; this keeps
    old bookmarks and any external links working by pointing at it.
    """
    from flask import redirect
    return redirect(f"/scenarios/{jid}", code=302)


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


