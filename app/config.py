"""Application configuration — every variable documented in .env.example."""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


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


@dataclass
class Settings:
    llm_provider: str = field(default_factory=lambda: _get("LLM_PROVIDER", "openai-compatible"))
    llm_base_url: str = field(default_factory=lambda: _get("LLM_BASE_URL", "http://localhost:11434/v1"))
    llm_api_key: str = field(default_factory=lambda: _get("LLM_API_KEY", ""))
    llm_primary_model: str = field(default_factory=lambda: _get("LLM_PRIMARY_MODEL", "gpt-4o-mini"))
    llm_strong_model: str = field(default_factory=lambda: _get("LLM_STRONG_MODEL", "") or _get("LLM_PRIMARY_MODEL", "gpt-4o-mini"))
    llm_cheap_model: str = field(default_factory=lambda: _get("LLM_CHEAP_MODEL", "") or _get("LLM_PRIMARY_MODEL", "gpt-4o-mini"))
    llm_embedding_model: str = field(default_factory=lambda: _get("LLM_EMBEDDING_MODEL", ""))
    llm_timeout_s: int = field(default_factory=lambda: _get_int("LLM_REQUEST_TIMEOUT_S", 120))
    llm_max_attempts: int = field(default_factory=lambda: _get_int("LLM_MAX_ATTEMPTS", 4))
    llm_backoff_base_s: float = field(default_factory=lambda: _get_float("LLM_BACKOFF_BASE_S", 1.5))
    database_path: str = field(default_factory=lambda: _get("DATABASE_PATH", "./data/vataic.db"))
    artifact_dir: str = field(default_factory=lambda: _get("ARTIFACT_DIR", "./data/artifacts"))
    budget_max_steps: int = field(default_factory=lambda: _get_int("BUDGET_MAX_STEPS", 40))
    budget_max_branches: int = field(default_factory=lambda: _get_int("BUDGET_MAX_BRANCHES", 20))
    budget_max_llm_calls: int = field(default_factory=lambda: _get_int("BUDGET_MAX_LLM_CALLS", 500))
    budget_max_tokens: int = field(default_factory=lambda: _get_int("BUDGET_MAX_TOKENS", 500000))
    budget_max_runtime_s: int = field(default_factory=lambda: _get_int("BUDGET_MAX_RUNTIME_S", 28800))
    beam_width: int = field(default_factory=lambda: _get_int("BEAM_WIDTH", 12))
    beam_reserved_divergent: int = field(default_factory=lambda: _get_int("BEAM_RESERVED_DIVERGENT", 3))
    merge_distance_threshold: float = field(default_factory=lambda: _get_float("MERGE_DISTANCE_THRESHOLD", 8.0))
    branch_prob_threshold: float = field(default_factory=lambda: _get_float("BRANCH_PROB_THRESHOLD", 0.15))
    tail_risk_impact_threshold: float = field(default_factory=lambda: _get_float("TAIL_RISK_IMPACT_THRESHOLD", 0.8))
    factor_max_macro: int = field(default_factory=lambda: _get_int("FACTOR_MAX_MACRO", 15))
    factor_max_personal: int = field(default_factory=lambda: _get_int("FACTOR_MAX_PERSONAL", 18))
    step_min_days: int = field(default_factory=lambda: _get_int("STEP_MIN_DAYS", 7))
    step_max_days: int = field(default_factory=lambda: _get_int("STEP_MAX_DAYS", 365))
    epoch_interval_steps: int = field(default_factory=lambda: _get_int("EPOCH_INTERVAL_STEPS", 4))
    context_token_budget: int = field(default_factory=lambda: _get_int("CONTEXT_TOKEN_BUDGET", 6000))
    secret_key: str = field(default_factory=lambda: _get("FLASK_SECRET_KEY", "change-me"))
    port: int = field(default_factory=lambda: _get_int("PORT", 5000))


settings = Settings()
