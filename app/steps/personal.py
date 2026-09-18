"""Personal projection: subjects, agency loops, enrichment, stopping, controls."""
import json, uuid
from app.db import get_conn

def create_subject(job_id: str, profile: dict, db_path=None) -> str:
    sid = uuid.uuid4().hex[:12]
    assumptions = []
    for f in ("risk_tolerance", "goals", "values_s", "support_system"):
        if not profile.get(f):
            assumptions.append(f"{f} defaulted")
    if not profile.get("risk_tolerance"): profile["risk_tolerance"] = "moderate"
    with get_conn(db_path) as c:
        c.execute("INSERT INTO subjects(id,job_id,privacy) VALUES(?,?, 'private')", (sid, job_id))
        c.execute("""INSERT OR REPLACE INTO subject_profiles(subject_id,facts,goals,constraints_s,
          values_s,risk_tolerance,agency,support_system,unknowns,assumptions)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (sid, json.dumps(profile.get("facts", {})), json.dumps(profile.get("goals", [])),
           json.dumps(profile.get("constraints", [])), json.dumps(profile.get("values", [])),
           profile.get("risk_tolerance", "moderate"), json.dumps(profile.get("agency", {})),
           json.dumps(profile.get("support", {})), json.dumps(profile.get("unknowns", [])),
           json.dumps(assumptions)))
    return sid

def get_subject(job_id: str, db_path=None) -> dict | None:
    with get_conn(db_path) as c:
        r = c.execute("""SELECT s.*, p.* FROM subjects s LEFT JOIN subject_profiles p
          ON p.subject_id=s.id WHERE s.job_id=?""", (job_id,)).fetchone()
        return dict(r) if r else None

def delete_personal(job_id: str, db_path=None) -> None:
    with get_conn(db_path) as c:
        c.execute("DELETE FROM subjects WHERE job_id=?", (job_id,))
        c.execute("DELETE FROM personal_factor_states WHERE job_id=?", (job_id,))
        c.execute("DELETE FROM personal_events WHERE job_id=?", (job_id,))

def personal_stopping(factors: dict, settings: dict) -> str | None:
    sl = settings.get("stop_loss")
    if sl is not None and factors.get("runway", 100) < sl: return "financial_stop_loss"
    if factors.get("goal_attainment", 0) >= 95: return "goal_reached"
    if factors.get("equilibrium", 0) >= 90: return "new_equilibrium"
    return None

def enrich_personal(outcome: dict, profile: dict | None = None) -> dict:
    outcome.setdefault("enrichment", {})
    outcome["enrichment"].update({"decision_levers": ["adjust effort", "seek support"],
      "protective_actions": ["maintain runway"], "warning_signs": ["burnout signals"],
      "support_needed": ["mentor"], "wellbeing_risks": ["stress"],
      "decision_map": [{"point": "continue vs pivot", "branches": ["stay", "change"]}],
      "framing": "Structured scenario exploration for decision support, not therapy/medical/legal/financial advice."})
    return outcome
