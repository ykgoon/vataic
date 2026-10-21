"""LLM gateway: provider-agnostic OpenAI-compatible client, routing, cache, retry, validators."""
import hashlib, json, time
import requests
from pydantic import BaseModel, ValidationError
from app.db import get_conn

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
    def __init__(self, base_url="", api_key="", model="", timeout_s=300, max_attempts=4,
                 backoff_base_s=1.5, db_path=None):
        from app.config import settings
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_primary_model
        self.timeout_s = timeout_s or settings.llm_timeout_s
        self.max_attempts = max_attempts or settings.llm_max_attempts
        self.backoff = backoff_base_s
        self.db_path = db_path

    @staticmethod
    def cache_key(phase: str, payload: dict, model: str, seed: int = 0) -> str:
        raw = json.dumps({"p": phase, "q": payload, "m": model, "s": seed}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key: h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def chat(self, job_id: str, phase: str, messages: list[dict], model: str = "",
             seed: int = 0, step: int = 0, particle: str = "") -> dict:
        """Cached + retried chat-completions call. Returns {'json':..., 'cache_hit':bool}."""
        mdl = model or self.model
        payload = {"messages": messages, "model": mdl}
        key = self.cache_key(phase, payload, mdl, seed)
        with get_conn(self.db_path) as c:
            r = c.execute("SELECT response FROM llm_calls WHERE cache_key=?", (key,)).fetchone()
            if r:
                return {"json": json.loads(r["response"]), "cache_hit": True, "key": key}
        # retry with exponential backoff (5.4)
        last = None
        for attempt in range(self.max_attempts):
            try:
                resp = requests.post(f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json={"model": mdl, "messages": messages, "response_format": {"type": "json_object"}},
                    timeout=self.timeout_s)
                if resp.status_code in (429,) or resp.status_code >= 500:
                    raise RuntimeError(f"transient {resp.status_code}")
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                parsed = json.loads(text)
                usage = data.get("usage") or {}
                try:
                    from app.llm.usage import record_llm_usage
                    record_llm_usage(job_id, phase, usage, mdl, db_path=self.db_path)
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
                    c.execute("""INSERT OR IGNORE INTO llm_calls(job_id,cache_key,phase,step_index,
                      particle_id,model,request,response,cache_hit) VALUES(?,?,?,?,?,?,?,?,0)""",
                      (job_id, key, phase, step, particle, mdl, json.dumps(payload), json.dumps(parsed)))
                return {"json": parsed, "cache_hit": False, "key": key}
            except Exception as e:
                last = e
                time.sleep(self.backoff * (2 ** attempt))
        raise RuntimeError(f"LLM call failed after {self.max_attempts}: {last}")

# ---------- role routing (5.2) ----------
ROLE_PHASES = {"strong": {"causal", "step", "explanation", "parse"},
               "cheap": {"epoch", "cluster"},
               "embedding": set()}

def model_for_role(role: str) -> str:
    from app.config import settings
    from app.db import get_conn
    try:
        with get_conn() as c:
            rows = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
        if role == "strong" and rows.get("llm.strong_model"): return rows["llm.strong_model"]
        if role == "cheap" and rows.get("llm.cheap_model"): return rows["llm.cheap_model"]
    except Exception: pass
    if role == "strong": return settings.llm_strong_model or settings.llm_primary_model
    if role == "cheap": return settings.llm_cheap_model or settings.llm_primary_model
    return settings.llm_primary_model

def model_for_phase(phase: str) -> str:
    for role, phases in ROLE_PHASES.items():
        if phase in phases: return model_for_role(role)
    from app.config import settings
    return settings.llm_primary_model
