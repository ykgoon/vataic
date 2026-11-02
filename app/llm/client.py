"""LLM gateway: provider-agnostic OpenAI-compatible client, routing, cache, retry, validators."""
import hashlib, json, logging, re, time
import requests
from pydantic import BaseModel, ValidationError
from app.db import get_conn

logger = logging.getLogger(__name__)

# Per-process circuit breaker: after this many consecutive provider failures
# without a success, callers should treat the provider as down and skip straight
# to heuristic fallbacks instead of paying the retry/timeout cost per call.
_BREAK_TS = {"failures": 0, "opened_at": 0.0}
_BREAK_THRESHOLD = 3
_BREAK_COOLDOWN_S = 45.0


def provider_open() -> bool:
    """True when the circuit allows another attempt."""
    if _BREAK_TS["failures"] < _BREAK_THRESHOLD:
        return True
    return (time.time() - _BREAK_TS["opened_at"]) >= _BREAK_COOLDOWN_S


def reset_breaker() -> None:
    """Clear the circuit breaker (e.g. tests, manual retry)."""
    _BREAK_TS["failures"] = 0
    _BREAK_TS["opened_at"] = 0.0


def _record_success() -> None:
    _BREAK_TS["failures"] = 0


def _record_failure() -> None:
    _BREAK_TS["failures"] += 1
    if _BREAK_TS["failures"] >= _BREAK_THRESHOLD:
        _BREAK_TS["opened_at"] = time.time()

# ---------- validators (5.5) ----------
class ParseOut(BaseModel):
    premise_type: str = "event"
    axioms: list[str] = []
    entities: list[str] = []
    systems: list[str] = []
    reality_mode: str = "grounded"
    horizon: str = "auto"

class FactorOut(BaseModel):
    factors: list[dict] = []
    discarded: list[dict] = []
    watchlist: list[str] = []

class CausalOut(BaseModel):
    edges: list[dict] = []

class StepOut(BaseModel):
    developments: list[dict] = []
    next_step_days: int = 90

class EpochOut(BaseModel):
    summary: str = ""
    turning_points: list[str] = []

class ClusterOut(BaseModel):
    labels: list[dict] = []

class ExplanationOut(BaseModel):
    explanation: str = ""
    indicators: list[str] = []
    assumptions: list[str] = []
    risks: list[str] = []

VALIDATORS = {"parse": ParseOut, "factors": FactorOut, "causal": CausalOut,
              "step": StepOut, "epoch": EpochOut, "cluster": ClusterOut,
              "explanation": ExplanationOut}

# ---------- client (5.1) ----------
class LLMClient:
    def __init__(self, base_url="", api_key="", model="", timeout_s=None,
                 max_attempts=None, backoff_base_s=None, db_path=None):
        """Defaults come from settings (env-configured); the old hardcoded
        defaults (300/4) silently overrode LLM_REQUEST_TIMEOUT_S etc. because
        `300 or settings.x` is always 300."""
        from app.config import settings
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        # Default model: the effective configured primary (DB settings row
        # first, then env). Blank is a valid value — the provider then
        # applies its own default model; no hardcoded fallback is
        # substituted ("gpt-4o-mini" broke every non-OpenAI provider).
        self.model = model or model_for_role("")
        self.timeout_s = timeout_s or settings.llm_timeout_s
        self.max_attempts = max_attempts or settings.llm_max_attempts
        self.backoff = backoff_base_s if backoff_base_s is not None else settings.llm_backoff_base_s
        self.db_path = db_path

    @staticmethod
    def cache_key(phase: str, payload: dict, model: str, seed: int = 0) -> str:
        raw = json.dumps({"p": phase, "q": payload, "m": model, "s": seed}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Parse model output robustly.

        OpenAI-compatible providers fronting local agent models routinely wrap
        JSON in markdown fences (```json ... ```) or add prose around it.
        json.loads on the raw content then raises, every retry repeats the
        failure, and the whole scenario silently degrades to heuristic fallbacks
        even though the provider answered correctly.
        """
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty model content")
        s = text.strip()
        try:
            return json.loads(s)
        except Exception:
            pass
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except Exception:
                pass
        raise ValueError(f"no JSON object in model content ({len(s)} chars)")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key: h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def chat(self, scenario_id: str, phase: str, messages: list[dict], model: str = "",
             seed: int = 0, step: int = 0, particle: str = "") -> dict:
        """Cached + retried chat-completions call. Returns {'json':..., 'cache_hit':bool}."""
        mdl = model or self.model
        payload = {"messages": messages, "model": mdl}
        key = self.cache_key(phase, payload, mdl, seed)
        with get_conn(self.db_path) as c:
            r = c.execute("SELECT response FROM llm_calls WHERE cache_key=?", (key,)).fetchone()
            if r:
                return {"json": json.loads(r["response"]), "cache_hit": True, "key": key}
        if not provider_open():
            # Half-open: allow one probe attempt per cooldown window so a
            # recovered provider is picked up; otherwise fail fast to the
            # heuristic fallback without paying timeout×attempts again.
            if (time.time() - _BREAK_TS["opened_at"]) >= _BREAK_COOLDOWN_S / 2:
                _BREAK_TS["opened_at"] = time.time()  # extend window; result updates below
            else:
                raise RuntimeError("LLM provider circuit open (recent failures)")
        # retry with exponential backoff (5.4). The read timeout escalates per
        # attempt: slow agent-gateway providers regularly take 2-3x the base
        # timeout on individual requests and still succeed eventually — a flat
        # timeout turned every attempt into a read timeout and silently
        # degraded whole scenarios to heuristic mode with zero recorded calls.
        last = None
        for attempt in range(self.max_attempts):
            read_to = min(int(self.timeout_s) * (attempt + 1), 600)
            try:
                resp = requests.post(f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json={"model": mdl, "messages": messages, "response_format": {"type": "json_object"}},
                    timeout=(min(10, self.timeout_s), read_to))
                if resp.status_code in (429,) or resp.status_code >= 500:
                    err = RuntimeError(f"transient {resp.status_code}")
                    if resp.status_code == 429:
                        err.response = resp
                    raise err
                resp.raise_for_status()
                data = resp.json()
                text = (data["choices"][0]["message"] or {}).get("content") or ""
                parsed = self._extract_json(text)
                _record_success()
                usage = data.get("usage") or {}
                try:
                    from app.llm.usage import record_llm_usage
                    record_llm_usage(scenario_id, phase, usage, mdl, db_path=self.db_path)
                except Exception:
                    pass  # usage accounting must never break the engine
                V = VALIDATORS.get(phase)
                if V:
                    try: parsed = V(**parsed).model_dump()
                    except ValidationError as e:
                        if attempt == self.max_attempts - 1: raise
                        messages = messages + [{"role": "user",
                            "content": f"Previous output failed validation: {e}. Return valid JSON."}]
                        time.sleep(self.backoff * (2 ** attempt))
                        continue
                with get_conn(self.db_path) as c:
                    c.execute("""INSERT OR IGNORE INTO llm_calls(scenario_id,cache_key,phase,step_index,
                      particle_id,model,request,response,cache_hit) VALUES(?,?,?,?,?,?,?,?,0)""",
                      (scenario_id, key, phase, step, particle, mdl, json.dumps(payload), json.dumps(parsed)))
                return {"json": parsed, "cache_hit": False, "key": key}
            except Exception as e:
                last = e
                if "FOREIGN KEY constraint failed" in str(e):
                    # The scenario row was deleted mid-call (user delete while
                    # this LLM call was in flight). Retrying the whole call
                    # burns provider quota for a scenario that no longer
                    # exists — abort immediately, this is not a provider
                    # failure so the circuit breaker stays untouched.
                    raise RuntimeError(
                        f"scenario {scenario_id} deleted mid-call; aborting LLM retry loop") from e
                logger.warning("LLM %s attempt %d/%d failed (model=%s): %s",
                               phase, attempt + 1, self.max_attempts, mdl, e)
                # Rate limiting needs a different cadence than transient
                # errors: a shared agent gateway can stay at its concurrency
                # cap for minutes, so back off long enough to actually catch
                # a free slot instead of burning all attempts in ~20s.
                ra = getattr(getattr(e, "response", None), "headers", {}).get("Retry-After") if type(e).__name__ == "HTTPError" else None
                if "429" in str(e):
                    try:
                        wait = float(ra) if ra else 0.0
                    except (TypeError, ValueError):
                        wait = 0.0
                    time.sleep(max(wait, 20, self.backoff * (2 ** attempt)))
                else:
                    time.sleep(self.backoff * (2 ** attempt))
        # One failed chat() call counts once against the breaker. Counting
        # every retry attempt meant a single bad call (4 timed-out attempts)
        # opened the breaker and degraded the rest of the scenario to heuristics.
        _record_failure()
        raise RuntimeError(f"LLM call failed after {self.max_attempts}: {last}")

# ---------- role routing (5.2) ----------
ROLE_PHASES = {"strong": {"causal", "step", "explanation", "parse", "chat"},
               "cheap": {"epoch", "cluster"},
               "embedding": set()}

def model_for_role(role: str) -> str:
    """Model for a role: DB settings rows first, then env config.

    Blank is honoured: an unset model is passed through as "" so the
    provider applies its own default model. Hardcoded fallbacks (e.g.
    "gpt-4o-mini") break every non-OpenAI provider and must not be
    resurrected here.
    """
    from app.config import settings
    from app.db import get_conn
    try:
        with get_conn() as c:
            rows = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
        if role == "strong" and rows.get("llm.strong_model"):
            return rows["llm.strong_model"]
        if role == "cheap" and rows.get("llm.cheap_model"):
            return rows["llm.cheap_model"]
        # A non-blank DB primary_model row wins for every role without its
        # own row. A blank DB row falls through to env (also usually blank),
        # so the end result is "" — the provider applies its own default
        # model. No hardcoded fallback may be substituted here: defaulting
        # to "gpt-4o-mini" broke every non-OpenAI provider.
        if rows.get("llm.primary_model"):
            return rows["llm.primary_model"].strip()
    except Exception:
        pass
    if role == "strong":
        return settings.llm_strong_model or ""
    if role == "cheap":
        return settings.llm_cheap_model or ""
    return settings.llm_primary_model

def model_for_phase(phase: str) -> str:
    for role, phases in ROLE_PHASES.items():
        if phase in phases: return model_for_role(role)
    from app.config import settings
    return settings.llm_primary_model
