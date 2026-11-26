"""Scenario routes: submission form, dashboard with htmx polling, detail + controls."""
import json
import logging
from flask import Blueprint, request, redirect, url_for, render_template, flash

logger = logging.getLogger(__name__)

bp = Blueprint("scenarios", __name__)


@bp.get("/scenarios/new")
def new_form():
    return render_template("scenarios/new.html")


@bp.post("/scenarios/new")
def new_scenario():
    from app.scenarios.repos import create_scenario
    jid = create_scenario(
        premise=request.form.get("premise", ""),
        scope=request.form.get("scope", "macro"),
        reality_mode=request.form.get("reality_mode", "grounded"),
        horizon=request.form.get("horizon", "auto"),
        outcome_count=request.form.get("outcome_count", "7"),
        decision_context=request.form.get("decision_context", ""),
    )
    # Spec: communicate that the projection may take hours and browser may be closed
    flash(
        f"Scenario {jid} accepted. Projections run asynchronously and may take a long time. "
        "You can close your browser after submitting; check back later for results.",
        "success",
    )
    return redirect(f"/scenarios/{jid}")


@bp.get("/scenarios")
def dashboard():
    from app.scenarios.repos import list_scenarios
    scenarios = list_scenarios()
    return render_template("scenarios/dashboard.html", scenarios=scenarios)


@bp.get("/scenarios/partial")
def dashboard_partial():
    from app.scenarios.repos import list_scenarios
    scenarios = list_scenarios()
    return render_template("scenarios/partial.html", scenarios=scenarios)


@bp.get("/scenarios/<jid>")
def detail(jid):
    from app.scenarios.repos import get_scenario, latest_checkpoint
    j = get_scenario(jid)
    if not j:
        # Deleted (or never existed): stale tabs and bookmarks land here
        # after a delete; render a friendly page instead of a 500 from
        # scenario["id"] on an empty dict.
        return render_template("scenarios/gone.html"), 404
    from app.db import get_conn
    from app.llm.usage import reconcile_budget
    from app.present import prepare_outcomes, repair_legacy_outcomes
    j = get_scenario(jid) or {}
    cp = latest_checkpoint(jid) or {}
    # Drop any legacy spend tally from budget_state; only the loop bounds
    # (steps, branches) are meaningful now.
    try:
        b = reconcile_budget(jid)
    except Exception:
        b = json.loads(j.get("budget_state", "{}")) if j.get("budget_state") else {}

    # Outcomes
    with get_conn() as c:
        outs = [dict(r) for r in c.execute(
            "SELECT * FROM outcomes WHERE scenario_id=?", (jid,))]
    try:
        repair_legacy_outcomes(jid)
        with get_conn() as c:
            outs = [dict(r) for r in c.execute(
                "SELECT * FROM outcomes WHERE scenario_id=?", (jid,))]
    except Exception:
        pass
    outcome_rows = prepare_outcomes(outs, jid)

    # No provider-spend panel: runs are not budgeted by LLM calls or tokens,
    # so neither is counted for display. Only the projection loop's own
    # bounds (steps, branches) are shown.
    caps = b.get("caps", {})
    steps_used = b.get("steps", 0) or j.get("current_step", 0) or 0
    steps_max = caps.get("steps", b.get("steps_max", 40)) or 40
    branches_used = b.get("branches", 0) or 0
    if not branches_used:
        # Pre-usage-tracking scenarios: derive from the durable particles table.
        try:
            with get_conn() as c:
                branches_used = c.execute(
                    "SELECT COUNT(*) FROM particles WHERE scenario_id=? AND parent_id IS NOT NULL",
                    (jid,)).fetchone()[0]
        except Exception:
            branches_used = 0
    branches_max = caps.get("branches", b.get("branches_max", 20)) or 20
    budget_pct = {
        "steps": round(steps_used / max(steps_max, 1) * 100),
        "branches": round(branches_used / max(branches_max, 1) * 100),
    }

    # Calculate progress: prefer the live checkpoint step, fall back to the
    # scenario row's current_step (kept fresh by the worker).
    phase = cp.get("phase") or j.get("current_phase", "")
    step = max(cp.get("step_index") or 0, j.get("current_step", 0) or 0)
    progress_pct = 0
    try:
        progress_pct = min(100, round(int(step) / max(int(steps_max), 1) * 100))
    except (ValueError, TypeError):
        progress_pct = 0

    # Calculate elapsed
    elapsed_text = "n/a"
    try:
        from datetime import datetime
        created = datetime.fromisoformat(j["created_at"]) if j.get("created_at") else None
        updated = datetime.fromisoformat(j["updated_at"]) if j.get("updated_at") else None
        if created and updated:
            elapsed = updated - created
            total_s = elapsed.total_seconds()
            if total_s < 60:
                elapsed_text = f"{int(total_s)}s"
            elif total_s < 3600:
                elapsed_text = f"{int(total_s/60)}m {int(total_s%60)}s"
            else:
                elapsed_text = f"{int(total_s/3600)}h {int((total_s%3600)/60)}m"
    except Exception:
        elapsed_text = "n/a"

    # Estimated completion (simple heuristic: remaining steps * checkpoint interval)
    eta_text = "n/a"
    try:
        if j.get("status") in ("RUNNING", "CHECKPOINTING"):
            from datetime import datetime, timedelta
            now = datetime.fromisoformat(j["updated_at"]) if j.get("updated_at") else datetime.utcnow()
            remaining_steps = max(40 - int(step), 0) if step else 40
            eta = now + timedelta(minutes=remaining_steps * 15)
            eta_text = eta.strftime("%H:%M %d %b")
    except Exception:
        eta_text = "n/a"

    # A completed scenario's page carries its own result: the futures view used
    # to live on a separate /results page behind a "View Results" button, so a
    # finished scenario was a status page with the answer one click away. The
    # futures sections (split, fan, map, story, outcome cards) render here now;
    # they need the same context that route built.
    futures_ctx = None
    if j.get("status") == "COMPLETED":
        try:
            from app.futures import load_futures_view
            from app.routes.results import _scope_subtitle, _with_colors
            from app.futures_svg import fan_svg, map_svg
            fv = load_futures_view(jid)
            if fv and fv.get("outcomes"):
                fv = _with_colors(fv)
                f0 = fv["factors"][0] if fv["factors"] else None
                fan_html, fan_legend = fan_svg(fv, f0["id"], f0["name"]) if f0 else ("", [])
                map_html, _cornerstones = map_svg(fv, jid)
                futures_ctx = {
                    "fv": fv,
                    "fan_html": fan_html,
                    "fan_legend": fan_legend,
                    "fan_factor": f0,
                    "map_html": map_html,
                    "story_o": fv["outcomes"][0],
                    "scope_subtitle": _scope_subtitle(fv.get("scope", "macro")),
                }
        except Exception:
            futures_ctx = None

    from app.routes.chat import panel_context
    # Backcast is shown on the page when it has been run, so the reader does not
    # have to press a button to find out what it said.
    stored_backcast = {}
    if j.get("status") == "COMPLETED":
        try:
            from app.backcast import load_backcast
            stored = load_backcast(jid)
            if stored:
                stored_backcast = {"outcomes": stored,
                                   "source": "llm" if any(
                                       (v or {}).get("source") == "llm"
                                       for v in stored.values()) else "simulation"}
        except Exception:
            stored_backcast = {}
    # The futures sections read fv / map_html / fan_* directly (they are shared
    # with the results template), so unpack the block into the context.
    ctx = dict(futures_ctx or {})
    return render_template("scenarios/detail.html",
        scenario=j, checkpoint=cp, budget=b,
        chat=panel_context(jid),
        progress_pct=progress_pct, elapsed_text=elapsed_text,
        futures=ctx or None, **ctx,
        outcomes=outcome_rows,
        backcast=stored_backcast or None,
        steps_used=steps_used, steps_max=steps_max,
        branches_used=branches_used, branches_max=branches_max,
        budget_pct=budget_pct, eta_text=eta_text)


def _ctrl(jid, action):
    from app.scenarios.repos import get_scenario, transition, _write_retry
    from app.db import get_conn
    j = get_scenario(jid) or {}
    st = j.get("status", "DRAFT")
    try:
        if action == "cancel" and st not in ("CANCELLED",):
            transition(jid, "CANCELLED", "user cancel")
        elif action == "suspend" and st in ("RUNNING", "CHECKPOINTING"):
            transition(jid, "SUSPENDED", "user suspend")
        elif action == "resume" and st == "SUSPENDED":
            transition(jid, "RUNNING", "user resume")
        elif action == "retry" and st in ("FAILED", "CANCELLED"):
            transition(jid, "QUEUED", "user retry")
        # Expire any live lease on cancel/suspend: the running worker's
        # renewal fails and it exits at its next boundary, while a later
        # retry/resume is picked up on the next scan instead of waiting out
        # the full lease TTL.
        if action in ("cancel", "suspend"):
            def _expire():
                with get_conn() as c:
                    c.execute("UPDATE leases SET expires_at=? WHERE scenario_id=?",
                              ("2000-01-01T00:00:00+00:00", jid))
            _write_retry(_expire)
    except Exception as e:
        return render_template("scenarios/status_badge.html", status=f"error: {e}")
    # Render the resulting state, not the action name: the badge swap replaces
    # the row's status span content until the next dashboard poll refreshes it.
    new = (get_scenario(jid) or {}).get("status", st)
    return render_template("scenarios/status_badge.html", status=new)


@bp.post("/scenarios/<jid>/cancel")
def cancel(jid):
    return _ctrl(jid, "cancel")


@bp.post("/scenarios/<jid>/suspend")
def suspend(jid):
    return _ctrl(jid, "suspend")


@bp.post("/scenarios/<jid>/resume")
def resume(jid):
    return _ctrl(jid, "resume")


@bp.post("/scenarios/<jid>/retry")
def retry(jid):
    return _ctrl(jid, "retry")


@bp.post("/scenarios/<jid>/delete")
def delete(jid):
    """Delete a scenario from any state: active runs are cancelled first so
    the worker aborts at its next boundary, then row, children, and on-disk
    artifacts are removed. The htmx confirm dialog is the consent gate; the
    server no longer refuses non-terminal deletes (stranded scenarios were
    effectively undeletable before)."""
    import shutil
    from app.scenarios.repos import get_scenario, delete_scenario, transition
    from app.config import settings
    from pathlib import Path
    j = get_scenario(jid) or {}
    if not j:
        # Already gone (double-click or raced delete): htmx callers get the
        # fresh partial; plain posts redirect back to the dashboard.
        if request.headers.get("HX-Request"):
            from app.scenarios.repos import list_scenarios
            return render_template("scenarios/partial.html", scenarios=list_scenarios())
        return redirect("/scenarios")
    if j.get("status") not in ("COMPLETED", "FAILED", "CANCELLED"):
        try:
            transition(jid, "CANCELLED", "user delete (auto-cancel)")
        except Exception:
            pass  # edge not allowed (e.g. raced state) - delete proceeds anyway
    delete_scenario(jid)
    shutil.rmtree(Path(settings.artifact_dir) / jid, ignore_errors=True)
    # htmx callers swap the target away; plain form posts (detail page)
    # get a real redirect — HX-Redirect plus a same-response swap raced
    # into "h:swapError" in real browsers.
    if request.headers.get("HX-Request"):
        # Swap fresh tbody content rather than just removing one row: a
        # concurrent 3s dashboard poll re-renders the tbody and orphans the
        # row node, so hx-swap="delete"/"outerHTML" on the stale row crashed
        # htmx (parentNode null -> swapError in the console). Both writers
        # now target the stable tbody element with identical fresh state.
        from app.scenarios.repos import list_scenarios
        return render_template("scenarios/partial.html", scenarios=list_scenarios())
    return redirect("/scenarios")


@bp.post("/scenarios/<jid>/re-explain")
def reexplain(jid):
    """Re-run the explanation phase for a completed scenario whose outcome cards
    carry only template narrative (e.g. explained while the LLM was down),
    then rebuild the futures-view artifact. Best-effort; the scenario stays
    COMPLETED either way."""
    from app.scenarios.repos import get_scenario, _write_retry
    from app.db import get_conn
    from app.steps.synthesis import explain
    from app.futures import build_futures_view, save_futures_view
    import json as _json
    scenario = get_scenario(jid) or {}
    if scenario.get("status") != "COMPLETED":
        return render_template("scenarios/status_badge.html", status="not completed")
    premise = scenario.get("premise", "")
    try:
        with get_conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM outcomes WHERE scenario_id=?", (jid,))]
        updated = 0
        for r in rows:
            def _j(v):
                try: return _json.loads(v) if v else []
                except Exception: return []
            o = {"name": r["name"], "probability": r["probability"], "band": r["band"],
                 "summary": r["summary"], "factor_states": _j(r.get("factor_states")),
                 "indicators": _j(r.get("indicators")), "assumptions": _j(r.get("assumptions")),
                 "risks": _j(r.get("risks")), "causal_path": _j(r.get("causal_path"))}
            e = explain(o, use_llm=True, scenario_id=jid, premise=premise)
            if e.get("explanation") and e["explanation"] != (r.get("explanation") or ""):
                def _upd(_e=e, _rid=r["id"]):
                    with get_conn() as c:
                        c.execute("UPDATE outcomes SET explanation=?, indicators=?, assumptions=?, risks=? WHERE id=?",
                                  (_e.get("explanation", ""), _json.dumps(_e.get("indicators", [])),
                                   _json.dumps(_e.get("assumptions", [])), _json.dumps(_e.get("risks", [])), _rid))
                _write_retry(_upd)
                updated += 1
        # Rebuild the futures-view artifact (updated_at bump forces rebuild).
        def _touch():
            with get_conn() as c:
                c.execute("UPDATE projection_scenarios SET updated_at=datetime('now') WHERE id=?", (jid,))
        _write_retry(_touch)
        fv = build_futures_view(jid)
        if fv:
            _write_retry(lambda: save_futures_view(jid, fv))
        return render_template("scenarios/status_badge.html", status=f"re-explained {updated}")
    except Exception as e:
        return render_template("scenarios/status_badge.html", status=f"error: {e}")



@bp.get("/scenarios/<jid>/backcast")
def backcast(jid):
    """Backcast one scenario: the decisions that would make each outcome happen.

    This renders into the page's own `#backcast-panel`, not the status badge:
    the old target had no id, so its result was never displayed at all. The
    panel is filled with a "working" state first and the result replaces it on
    the same request, so the reader sees the work and then the answer.
    """
    from app.scenarios.repos import get_scenario
    from app.backcast import build_backcast, save_backcast
    scenario = get_scenario(jid) or {}
    if not scenario:
        return render_template("scenarios/backcast_panel.html", jid=jid,
                               error="Scenario not found.")
    if scenario.get("status") != "COMPLETED":
        return render_template(
            "scenarios/backcast_panel.html", jid=jid,
            error="Backcasting needs a completed projection — there are no "
                  "outcomes to work backwards from yet.")
    try:
        result = build_backcast(jid)
        if result.get("outcomes"):
            save_backcast(jid, result)
            return render_template("scenarios/backcast_panel.html", jid=jid,
                                   backcast=result, outcomes=_outcomes_for(jid))
        return render_template(
            "scenarios/backcast_panel.html", jid=jid,
            error="This scenario produced no outcomes, so there is nothing to "
                  "backcast from.")
    except Exception as e:
        return render_template("scenarios/backcast_panel.html", jid=jid,
                               error=f"Backcast failed: {e}")


@bp.get("/scenarios/<jid>/backcast/<oid>/model")
def backcast_model(jid, oid):
    """Model reading for one outcome, layered over the deterministic panel.

    Separate request per outcome so the page never waits on a provider: each
    card upgrades itself if the call succeeds and keeps the derived reading if
    it does not.
    """
    from app.backcast import enhance_outcome, load_backcast, set_outcome_backcast
    try:
        entry = enhance_outcome(jid, oid)
        if entry:
            set_outcome_backcast(jid, oid, entry)
            return render_template("scenarios/backcast_card.html", jid=jid,
                                   oid=oid, b=entry, o=_outcome_row(jid, oid))
    except Exception as e:
        logger.warning("backcast model upgrade failed for %s/%s: %s", jid, oid, e)
    # No model reading: re-render the stored deterministic card unchanged rather
    # than replacing good content with an error. The card's own source badge
    # already says the reading is derived from the simulation, so the page stays
    # honest without showing an error the reader cannot act on.
    stored = (load_backcast(jid) or {}).get(oid)
    if stored:
        return render_template("scenarios/backcast_card.html", jid=jid, oid=oid,
                               b=stored, o=_outcome_row(jid, oid))
    return "", 204


def _outcome_row(jid, oid):
    from app.db import get_conn
    with get_conn() as c:
        r = c.execute("SELECT id, name, band, probability, summary FROM outcomes "
                      "WHERE id=? AND scenario_id=?", (oid, jid)).fetchone()
    return dict(r) if r else {}


def _outcomes_for(jid):
    """Outcome rows keyed for the backcast panel (name/probability, newest first)."""
    from app.db import get_conn
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, name, band, probability, summary FROM outcomes "
            "WHERE scenario_id=? ORDER BY probability DESC", (jid,))]

@bp.get("/scenarios/<jid>/review")
def review(jid):
    from app.db import get_conn
    with get_conn() as c:
        s = c.execute("SELECT * FROM scenario_inputs WHERE scenario_id=?", (jid,)).fetchone()
        f = c.execute("SELECT * FROM factor_sets WHERE scenario_id=? ORDER BY id DESC LIMIT 1", (jid,)).fetchone()
        g = c.execute("SELECT * FROM causal_graphs WHERE scenario_id=? ORDER BY id DESC LIMIT 1", (jid,)).fetchone()

    scenario = {"assumptions": s["assumptions"] if s else "[]"} if s else None
    factors = {"factors": f["factors"] if f else "[]", "watchlist": f["watchlist"] if f else "[]"} if f else None
    graph = {"edges": g["edges"] if g else "[]"} if g else None

    return render_template("scenarios/review.html", scenario=jid, parsed=scenario, factors=factors, graph=graph)


@bp.post("/scenarios/<jid>/review")
def review_save(jid):
    from app.db import get_conn
    fac = request.form.get("factors", "[]")
    with get_conn() as c:
        c.execute("INSERT INTO factor_sets(scenario_id,factors) VALUES(?,?)", (jid, fac))
    return redirect(f"/scenarios/{jid}")