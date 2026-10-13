"""Flask application factory with proper template rendering."""
import os
from flask import Flask, render_template


def create_app(config_override: dict | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "change-me")
    if config_override:
        app.config.update(config_override)

    # Auto-migrate schema on startup (idempotent).
    try:
        from app.migrate import apply_pending
        apply_pending()
    except Exception:
        pass  # surfacing deploy errors is handled by /healthz

    @app.get("/")
    def index():
        from app.jobs.repositories import list_jobs
        return render_template("jobs/dashboard.html", jobs=list_jobs())

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # Feature blueprints (registered lazily so hello-world works standalone).
    for mod, attr in [
        ("app.routes.jobs", "bp"),
        ("app.routes.results", "bp"),
        ("app.routes.personal", "bp"),
        ("app.routes.settings", "bp"),
    ]:
        try:
            m = __import__(mod, fromlist=[attr])
            app.register_blueprint(getattr(m, attr))
        except Exception:
            continue

    return app


app = create_app()
