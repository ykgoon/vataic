"""Job routes: submission form, dashboard with htmx polling, detail + controls."""
import json
from flask import Blueprint, request, redirect, url_for, render_template

bp = Blueprint("jobs", __name__)


@bp.get("/jobs/new")
def new_form():
    return render_template("jobs/new.html")


@bp.post("/jobs/new")
def new_job():
    from app.jobs.repositories import create_job
    jid = create_job(
        request.form.get("premise", ""),
        request.form.get("scope", "macro"),
        request.form.get("reality_mode", "grounded"),
        request.form.get("horizon", "auto"),
        request.form.get("outcome_count", "7"),
        request.form.get("budget_limit", "500000"),
        request.form.get("detail_level", "standard"),
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
    j = get_job(jid) or {}
    cp = latest_checkpoint(jid) or {}
    b = json.loads(j.get("budget_state", "{}")) if j.get("budget_state") else {}

    # Calculate progress
    phase = j.get("current_phase", "")
    step = j.get("current_step", 0)
    progress_pct = 0
    if step and phase:
        try:
            progress_pct = int(step) * 10
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

    return render_template("jobs/detail.html",
        job=j, checkpoint=cp, budget=b,
        progress_pct=progress_pct, elapsed_text=elapsed_text)


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
