"""Application configuration.

All settings live in the SQLite `settings` table (DB is the single source of
truth; the web UI edits it directly on /settings). Environment variables are
honoured only as bootstrap fallbacks for values with no DB row yet — a fresh
deployment starts with the documented defaults, and operators who prefer env
config can still set variables without touching the DB. Once a value is
written to the settings table it wins.

The LLM API key is stored in the DB (llm.api_key, write-only: never
rendered back by the UI) with LLM_API_KEY env as fallback; the Flask secret
stays env-only. VATAIC_ALLOW_HEURISTIC, VATAIC_DB_WRITE_ATTEMPTS/BACKOFF
and PORT remain process-level knobs.
"""
import os
from dataclasses import dataclass, field


def _get(name: str, default: str = "") -> str:
    # An env var that is present but empty counts as unset: compose/env_file
    # deployments commonly ship `VAR=` placeholders, and those must not mask
    # the documented defaults (e.g. LLM_PRIMARY_MODEL=).
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _db_setting(key: str) -> str:
    """Read one value from the settings table. Returns "" on any failure so
    callers fall back to env/default — config reads must never crash the app
    (e.g. before the first migrate ran)."""
    try:
        from app.db import get_conn
        with get_conn() as c:
            r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return (r["value"] or "") if r else ""
    except Exception:
        return ""


def _cfg(name: str, key: str, default: str = "") -> str:
    """DB settings row first; env var is the fallback; then the default."""
    v = _db_setting(key)
    if v != "":
        return v
    return _get(name, default)


def _cfg_int(name: str, key: str, default: int) -> int:
    v = _cfg(name, key, "")
    if v == "":
        return default
    try:
        return int(float(v))
    except ValueError:
        return default


def _cfg_float(name: str, key: str, default: float) -> float:
    v = _cfg(name, key, "")
    if v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


@dataclass
class Settings:
    # The only provider is openai-compatible; pinned, not configurable.
    llm_provider: str = "openai-compatible"
    llm_base_url: str = field(default_factory=lambda: _cfg("LLM_BASE_URL", "llm.base_url", "http://localhost:11434/v1"))
    # Blank model is a deliberate config: the provider then applies its own
    # default model (verified against the target gateway). No hardcoded
    # fallback — a provider-specific hardcoded default breaks every
    # non-OpenAI provider.
    llm_primary_model: str = field(default_factory=lambda: _cfg("LLM_PRIMARY_MODEL", "llm.primary_model", ""))
    llm_strong_model: str = field(default_factory=lambda: _cfg("LLM_STRONG_MODEL", "llm.strong_model", ""))
    llm_cheap_model: str = field(default_factory=lambda: _cfg("LLM_CHEAP_MODEL", "llm.cheap_model", ""))
    llm_embedding_model: str = field(default_factory=lambda: _cfg("LLM_EMBEDDING_MODEL", "llm.embedding_model", ""))
    llm_timeout_s: int = field(default_factory=lambda: _cfg_int("LLM_REQUEST_TIMEOUT_S", "llm.timeout_s", 120))
    llm_max_attempts: int = field(default_factory=lambda: _cfg_int("LLM_MAX_ATTEMPTS", "llm.max_attempts", 4))
    llm_backoff_base_s: float = field(default_factory=lambda: _cfg_float("LLM_BACKOFF_BASE_S", "llm.backoff_base_s", 1.5))
    database_path: str = field(default_factory=lambda: _get("DATABASE_PATH", "./data/vataic.db"))
    artifact_dir: str = field(default_factory=lambda: _get("ARTIFACT_DIR", "./data/artifacts"))
    budget_max_steps: int = field(default_factory=lambda: _cfg_int("BUDGET_MAX_STEPS", "budget.max_steps", 40))
    budget_max_branches: int = field(default_factory=lambda: _cfg_int("BUDGET_MAX_BRANCHES", "budget.max_branches", 20))
    budget_max_runtime_s: int = field(default_factory=lambda: _cfg_int("BUDGET_MAX_RUNTIME_S", "budget.max_runtime_s", 28800))
    beam_width: int = field(default_factory=lambda: _cfg_int("BEAM_WIDTH", "beam.width", 12))
    beam_reserved_divergent: int = field(default_factory=lambda: _cfg_int("BEAM_RESERVED_DIVERGENT", "beam.reserved_divergent", 3))
    merge_distance_threshold: float = field(default_factory=lambda: _cfg_float("MERGE_DISTANCE_THRESHOLD", "merge.distance_threshold", 8.0))
    branch_prob_threshold: float = field(default_factory=lambda: _cfg_float("BRANCH_PROB_THRESHOLD", "branch.prob_threshold", 0.15))
    tail_risk_impact_threshold: float = field(default_factory=lambda: _cfg_float("TAIL_RISK_IMPACT_THRESHOLD", "tail_risk.impact_threshold", 0.8))
    factor_max_macro: int = field(default_factory=lambda: _cfg_int("FACTOR_MAX_MACRO", "factors.max_macro", 15))
    factor_max_personal: int = field(default_factory=lambda: _cfg_int("FACTOR_MAX_PERSONAL", "factors.max_personal", 18))
    step_min_days: int = field(default_factory=lambda: _cfg_int("STEP_MIN_DAYS", "step.min_days", 7))
    step_max_days: int = field(default_factory=lambda: _cfg_int("STEP_MAX_DAYS", "step.max_days", 365))
    epoch_interval_steps: int = field(default_factory=lambda: _cfg_int("EPOCH_INTERVAL_STEPS", "epoch.interval_steps", 4))
    context_token_budget: int = field(default_factory=lambda: _cfg_int("CONTEXT_TOKEN_BUDGET", "context.token_budget", 6000))
    port: int = field(default_factory=lambda: _get_int("PORT", 5000))


settings = Settings()


def refresh() -> None:
    """Reload mutable fields after a settings save (multi-field form POSTs
    arrive as one request; the dataclass snapshots values at import time)."""
    f = settings.__dataclass_fields__
    for name in f:
        if name in ("database_path", "artifact_dir", "port", "llm_provider"):
            continue  # process knobs / pinned values: not DB-refreshable
        default_factory = f[name].default_factory
        if default_factory is not __import__("dataclasses").MISSING:
            setattr(settings, name, default_factory())


# DB key -> env var for the one-time bootstrap (env value fills a settings
# row that is still missing or still at its untouched seed default). Runs at
# migration/startup time so an upgrade from the env-configured deployment
# keeps its effective values losslessly; after that the DB is the source and
# the settings page is authoritative.
ENV_BOOTSTRAP = {
    "llm.api_key": "LLM_API_KEY",
    "llm.base_url": "LLM_BASE_URL",
    "llm.primary_model": "LLM_PRIMARY_MODEL",
    "llm.strong_model": "LLM_STRONG_MODEL",
    "llm.cheap_model": "LLM_CHEAP_MODEL",
    "llm.embedding_model": "LLM_EMBEDDING_MODEL",
    "llm.timeout_s": "LLM_REQUEST_TIMEOUT_S",
    "llm.max_attempts": "LLM_MAX_ATTEMPTS",
    "budget.max_steps": "BUDGET_MAX_STEPS",
    "budget.max_branches": "BUDGET_MAX_BRANCHES",
    "budget.max_runtime_s": "BUDGET_MAX_RUNTIME_S",
    "beam.width": "BEAM_WIDTH",
    "beam.reserved_divergent": "BEAM_RESERVED_DIVERGENT",
    "merge.distance_threshold": "MERGE_DISTANCE_THRESHOLD",
    "branch.prob_threshold": "BRANCH_PROB_THRESHOLD",
    "tail_risk.impact_threshold": "TAIL_RISK_IMPACT_THRESHOLD",
    "factors.max_macro": "FACTOR_MAX_MACRO",
    "factors.max_personal": "FACTOR_MAX_PERSONAL",
    "step.min_days": "STEP_MIN_DAYS",
    "step.max_days": "STEP_MAX_DAYS",
    "epoch.interval_steps": "EPOCH_INTERVAL_STEPS",
    "context.token_budget": "CONTEXT_TOKEN_BUDGET",
}


def bootstrap_env_to_db(db_path=None) -> list[str]:
    """Copy set env vars into settings rows that are missing or untouched.

    Never touches api keys, the Flask secret, or process paths (DATABASE_PATH,
    ARTIFACT_DIR, PORT stay env-only). Returns the keys it wrote.
    """
    from app.seed import DEFAULTS
    wrote = []
    try:
        from app.db import get_conn
        with get_conn(db_path) as c:
            for key, env_name in ENV_BOOTSTRAP.items():
                env_v = _get(env_name, "")
                if not env_v:
                    continue
                r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
                cur = r["value"] if r else None
                untouched = cur is None or cur == "" or (
                    key in DEFAULTS and cur == DEFAULTS[key])
                if untouched and cur != env_v:
                    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                              (key, env_v))
                    wrote.append(key)
    except Exception:
        return []  # config bootstrap must never block startup
    return wrote


def flask_secret_key() -> str:
    """Flask session key: env only (FLASK_SECRET_KEY), never the DB."""
    return _get("FLASK_SECRET_KEY", "change-me")


def llm_api_key() -> str:
    """LLM API key: stored settings row first (llm.api_key), env fallback.

    Write-only from the UI: the row is never rendered back; a blank save
    keeps the existing key."""
    v = _db_setting("llm.api_key")
    if v:
        return v
    return _get("LLM_API_KEY", "")


def allow_heuristic() -> bool:
    """Heuristic escape hatch: env-only, must not be editable in the UI."""
    return os.environ.get("VATAIC_ALLOW_HEURISTIC", "") in ("1", "true", "yes")
