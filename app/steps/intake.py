"""Scenario intake: parse premise, ambiguity defaults, reality modes, crisis gate."""
import json, re
from app.db import get_conn
from app.jobs.repositories import idem_key, idem_get, idem_put

CRISIS_KEYWORDS = {"suicide", "self-harm", "self harm", "kill myself", "end my life",
                   "abuse", "severe depression", "immediate danger"}
SAFETY_RESOURCES = ("If you are in crisis, please contact local emergency services (e.g. 911) "
  "or a crisis helpline such as 988 (US). Consider reaching out to a trusted person or professional.")

DEFAULT_HORIZON = "auto"

def detect_crisis(premise: str) -> bool:
    low = premise.lower()
    return any(k in low for k in CRISIS_KEYWORDS)

def heuristic_parse(premise: str, scope: str, reality_mode: str, horizon: str) -> dict:
    axioms = [premise.strip()]
    entities = re.findall(r"[A-Z][a-z]+(?:\s[A-Z][a-z]+)*", premise)[:8]
    low = premise.lower()
    ptype = "personal" if any(w in low for w in ("i ", "my ", "career", "job", "marriage")) else "event"
    return {"premise_type": ptype, "axioms": axioms, "entities": entities,
            "systems": ["economy"] if "price" in low or "house" in low else ["general"],
            "reality_mode": reality_mode, "horizon": horizon or DEFAULT_HORIZON}

def parse_scenario(job_id: str, premise: str, scope="macro", reality_mode="grounded",
                   horizon="auto", use_llm: bool = False, db_path=None) -> dict:
    key = idem_key(job_id, "parse", 0, "", premise[:64])
    cached = idem_get(key, db_path)
    if cached: return cached
    assumptions = []
    if not horizon: horizon = DEFAULT_HORIZON; assumptions.append("horizon defaulted to auto")
    # crisis gate (personal scope halts; macro logs only)
    crisis = detect_crisis(premise)
    if crisis and scope == "personal":
        with get_conn(db_path) as c:
            c.execute("INSERT INTO safety_flags(job_id,flagged,reason,resources) VALUES(?,?,?,?)",
                      (job_id, 1, "crisis-topic detected", SAFETY_RESOURCES))
        result = {"halted": True, "reason": "crisis", "resources": SAFETY_RESOURCES,
                  "assumptions": assumptions}
        idem_put(key, result, db_path)
        return result
    if use_llm:
        try:
            from app.llm.client import LLMClient, model_for_phase
            out = LLMClient(db_path=db_path).chat(job_id, "parse", [
                {"role": "system", "content": "Parse premise into JSON: premise_type, axioms, entities, systems, reality_mode, horizon."},
                {"role": "user", "content": premise}], model=model_for_phase("parse"))["json"]
        except Exception:
            out = heuristic_parse(premise, scope, reality_mode, horizon)
    else:
        out = heuristic_parse(premise, scope, reality_mode, horizon)
    out.setdefault("assumptions", assumptions)
    out["halted"] = False
    with get_conn(db_path) as c:
        c.execute("""INSERT OR REPLACE INTO scenario_inputs(job_id,premise,premise_type,axioms,
          entities,systems,reality_mode,horizon,assumptions) VALUES(?,?,?,?,?,?,?,?,?)""",
          (job_id, premise, out.get("premise_type", "event"), json.dumps(out.get("axioms", [])),
           json.dumps(out.get("entities", [])), json.dumps(out.get("systems", [])),
           out.get("reality_mode", reality_mode), out.get("horizon", horizon),
           json.dumps(out.get("assumptions", []))))
    idem_put(key, out, db_path)
    return out
