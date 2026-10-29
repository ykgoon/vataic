"""Job routes: submission form, dashboard with htmx polling, detail + controls."""
import json
from flask import Blueprint, request, redirect, url_for, render_template, flash

bp = Blueprint("jobs", __name__)


@bp.get("/jobs/new")
def new_form():
    return render_template("jobs/new.html")


@bp.post("/jobs/new")
def new_job():
    from app.jobs.repositories import create_job
    jid = create_job(
        premise=request.form.get("premise", ""),
        scope=request.form.get("scope", "macro"),
        reality_mode=request.form.get("reality_mode", "grounded"),
        horizon=request.form.get("horizon", "auto"),
        outcome_count=request.form.get("outcome_count", "7"),
    )
    # Spec: communicate that the projection may take hours and browser may be closed
    flash(
        f"Job {jid} accepted. Projections run asynchronously and may take a long time. "
        "You can close your browser after submitting; check back later for results.",
        "success",
    )
    return redirect(f"/jobs/{jid}")


@bp.get("/jobs")
def dashboard():
    from app.jobs.repositories import list_jobs
    jobs = list_jobs()
    return render_template("jobs/dashboard.html", jobs=jobs)


@bp.get("/jobs/partial")
def dashboard_partial():
    from app.jobs.repositories import list_jobs
    jobs = list_jobs()
    return render_template("jobs/partial.html", jobs=jobs)


@bp.get("/jobs/<jid>")
def detail(jid):
    from app.jobs.repositories import get_job, latest_checkpoint
    from app.db import get_conn
    from app.llm.usage import reconcile_budget, job_usage_summary
    from app.present import prepare_outcomes, repair_legacy_outcomes
    j = get_job(jid) or {}
    cp = latest_checkpoint(jid) or {}
    # Reconcile budget_state with the durable llm_calls ledger so usage is
    # real even for jobs that ran before usage capture existed.
    try:
        b = reconcile_budget(jid)
    except Exception:
        b = json.loads(j.get("budget_state", "{}")) if j.get("budget_state") else {}

    # Outcomes
    with get_conn() as c:
        outs = [dict(r) for r in c.execute(
            "SELECT * FROM outcomes WHERE job_id=?", (jid,))]
    try:
        repair_legacy_outcomes(jid)
        with get_conn() as c:
            outs = [dict(r) for r in c.execute(
                "SELECT * FROM outcomes WHERE job_id=?", (jid,))]
    except Exception:
        pass
    outcome_rows = prepare_outcomes(outs, jid)

    # LLM call stats: budget_state is reconciled above; cross-check with the
    # llm_calls table and take the larger of the two.
    usage = job_usage_summary(jid)
    llm_calls = max(b.get("llm_calls", 0) or 0, usage["real_calls"])
    llm_tokens = max(b.get("tokens", 0) or 0, usage["tokens"])

    # Budget usage percentages (from budget_state: used + caps)
    caps = b.get("caps", {})
    llm_calls_used = llm_calls
    tokens_used = llm_tokens
    steps_used = b.get("steps", 0) or j.get("current_step", 0) or 0
    llm_calls_max = caps.get("llm_calls", b.get("llm_calls_max", 500)) or 500
    tokens_max = caps.get("tokens", b.get("tokens_max", 500000)) or 500000
    steps_max = caps.get("steps", b.get("steps_max", 40)) or 40
    branches_used = b.get("branches", 0) or 0
    if not branches_used:
        # Pre-usage-tracking jobs: derive from the durable particles table.
        try:
            with get_conn() as c:
                branches_used = c.execute(
                    "SELECT COUNT(*) FROM particles WHERE job_id=? AND parent_id IS NOT NULL",
                    (jid,)).fetchone()[0]
        except Exception:
            branches_used = 0
    branches_max = caps.get("branches", b.get("branches_max", 20)) or 20
    budget_pct = {
        "llm_calls": round(llm_calls_used / max(llm_calls_max, 1) * 100),
        "tokens": round(tokens_used / max(tokens_max, 1) * 100),
        "steps": round(steps_used / max(steps_max, 1) * 100),
        "branches": round(branches_used / max(branches_max, 1) * 100),
    }

    # Calculate progress: prefer the live checkpoint step, fall back to the
    # job row's current_step (kept fresh by the worker).
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

    return render_template("jobs/detail.html",
        job=j, checkpoint=cp, budget=b,
        progress_pct=progress_pct, elapsed_text=elapsed_text,
        outcomes=outcome_rows,
        llm_calls=llm_calls, llm_tokens=llm_tokens,
        steps_used=steps_used, steps_max=steps_max,
        branches_used=branches_used, branches_max=branches_max,
        llm_calls_max=llm_calls_max, tokens_max=tokens_max,
        budget_pct=budget_pct, eta_text=eta_text)


def _ctrl(jid, action):
    from app.jobs.repositories import get_job, transition
    j = get_job(jid) or {}
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
    except Exception as e:
        return render_template("jobs/status_badge.html", status=f"error: {e}")
    return render_template("jobs/status_badge.html", status=action)


@bp.post("/jobs/<jid>/cancel")
def cancel(jid):
    return _ctrl(jid, "cancel")


@bp.post("/jobs/<jid>/suspend")
def suspend(jid):
    return _ctrl(jid, "suspend")


@bp.post("/jobs/<jid>/resume")
def resume(jid):
    return _ctrl(jid, "resume")


@bp.post("/jobs/<jid>/retry")
def retry(jid):
    return _ctrl(jid, "retry")


@bp.post("/jobs/<jid>/re-explain")
def reexplain(jid):
    """Re-run the explanation phase for a completed job whose outcome cards
    carry only template narrative (e.g. explained while the LLM was down),
    then rebuild the futures-view artifact. Best-effort; the job stays
    COMPLETED either way."""
    from app.jobs.repositories import get_job, _write_retry
    from app.db import get_conn
    from app.steps.synthesis import explain
    from app.futures import build_futures_view, save_futures_view
    import json as _json
    job = get_job(jid) or {}
    if job.get("status") != "COMPLETED":
        return render_template("jobs/status_badge.html", status="not completed")
    premise = job.get("premise", "")
    try:
        with get_conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM outcomes WHERE job_id=?", (jid,))]
        updated = 0
        for r in rows:
            def _j(v):
                try: return _json.loads(v) if v else []
                except Exception: return []
            o = {"name": r["name"], "probability": r["probability"], "band": r["band"],
                 "summary": r["summary"], "factor_states": _j(r.get("factor_states")),
                 "indicators": _j(r.get("indicators")), "assumptions": _j(r.get("assumptions")),
                 "risks": _j(r.get("risks")), "causal_path": _j(r.get("causal_path"))}
            e = explain(o, use_llm=True, job_id=jid, premise=premise)
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
                c.execute("UPDATE projection_jobs SET updated_at=datetime('now') WHERE id=?", (jid,))
        _write_retry(_touch)
        fv = build_futures_view(jid)
        if fv:
            _write_retry(lambda: save_futures_view(jid, fv))
        return render_template("jobs/status_badge.html", status=f"re-explained {updated}")
    except Exception as e:
        return render_template("jobs/status_badge.html", status=f"error: {e}")


@bp.get("/jobs/<jid>/review")
def review(jid):
    from app.db import get_conn
    with get_conn() as c:
        s = c.execute("SELECT * FROM scenario_inputs WHERE job_id=?", (jid,)).fetchone()
        f = c.execute("SELECT * FROM factor_sets WHERE job_id=? ORDER BY id DESC LIMIT 1", (jid,)).fetchone()
        g = c.execute("SELECT * FROM causal_graphs WHERE job_id=? ORDER BY id DESC LIMIT 1", (jid,)).fetchone()

    scenario = {"assumptions": s["assumptions"] if s else "[]"} if s else None
    factors = {"factors": f["factors"] if f else "[]", "watchlist": f["watchlist"] if f else "[]"} if f else None
    graph = {"edges": g["edges"] if g else "[]"} if g else None

    return render_template("jobs/review.html", job=jid, scenario=scenario, factors=factors, graph=graph)


@bp.post("/jobs/<jid>/review")
def review_save(jid):
    from app.db import get_conn
    fac = request.form.get("factors", "[]")
    with get_conn() as c:
        c.execute("INSERT INTO factor_sets(job_id,factors) VALUES(?,?)", (jid, fac))
    return redirect(f"/jobs/{jid}")