"""Repositories: jobs, checkpoints, leases, idempotency, budget, recovery, events."""
import json, os, sqlite3, time as _time, uuid, hashlib
from datetime import datetime, timedelta, timezone
from app.db import get_conn
from app.jobs.states import validate


def _write_retry(fn, *args, **kw):
    """Run a DB write, retrying transient 'database is locked' errors.

    busy_timeout already absorbs short contention; this covers longer spikes
    (e.g. single-core starvation) so a lock blip degrades instead of crashing
    a running job or breaking lease renewal.
    """
    attempts = int(os.environ.get("VATAIC_DB_WRITE_ATTEMPTS", "3"))
    base = float(os.environ.get("VATAIC_DB_WRITE_BACKOFF_S", "0.5"))
    for i in range(max(1, attempts)):
        try:
            return fn(*args, **kw)
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and i < attempts - 1:
                _time.sleep(base * (2 ** i))
                continue
            raise

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# ---- jobs ----
def create_job(premise: str, scope="macro", reality_mode="grounded", horizon="auto",
               outcome_count=7, project_id="default", db_path=None) -> str:
    jid = uuid.uuid4().hex[:12]
    with get_conn(db_path) as c:
        c.execute("INSERT OR IGNORE INTO projects(id,name) VALUES('default','default')");
        c.execute("""INSERT INTO projection_jobs(id,project_id,premise,scope,reality_mode,
          horizon,outcome_count,status) VALUES(?,?,?,?,?,?,?, 'QUEUED')""",
          (jid, project_id, premise, scope, reality_mode, horizon, outcome_count))
        c.execute("INSERT INTO job_events(job_id,from_state,to_state,message) VALUES(?,?,?,?)",
          (jid, "DRAFT", "QUEUED", "job accepted"))
    return jid

def get_job(jid: str, db_path=None) -> dict | None:
    with get_conn(db_path) as c:
        r = c.execute("SELECT * FROM projection_jobs WHERE id=?", (jid,)).fetchone()
        return dict(r) if r else None

def transition(jid: str, to_state: str, message="", db_path=None) -> dict:
    from app.db import get_conn as _gc
    def _once():
        with _gc(db_path) as c:
            r = c.execute("SELECT status FROM projection_jobs WHERE id=?", (jid,)).fetchone()
            if not r: raise KeyError(jid)
            frm = r["status"]
            validate(frm, to_state)
            c.execute("UPDATE projection_jobs SET status=?, updated_at=? WHERE id=?",
                      (to_state, now_iso(), jid))
            c.execute("INSERT INTO job_events(job_id,from_state,to_state,message) VALUES(?,?,?,?)",
                      (jid, frm, to_state, message))
            return {"from": frm, "to": to_state}
    return _write_retry(_once)

def append_event(jid: str, frm: str, to: str, message="", db_path=None) -> None:
    with get_conn(db_path) as c:
        c.execute("INSERT INTO job_events(job_id,from_state,to_state,message) VALUES(?,?,?,?)",
                  (jid, frm, to, message))

def list_jobs(db_path=None) -> list[dict]:
    with get_conn(db_path) as c:
        return [dict(r) for r in c.execute("SELECT * FROM projection_jobs ORDER BY created_at DESC")]

# ---- checkpoints (3.2) ----
def save_checkpoint(jid: str, phase: str, step: int, payload: dict, budget: dict, db_path=None) -> int:
    def _once():
        with get_conn(db_path) as c:
            cur = c.execute("""INSERT INTO checkpoints(job_id,phase,step_index,payload,budget,immutable)
              VALUES(?,?,?,?,?,1)""", (jid, phase, step, json.dumps(payload), json.dumps(budget)))
            return cur.lastrowid
    return _write_retry(_once)

def latest_checkpoint(jid: str, db_path=None) -> dict | None:
    with get_conn(db_path) as c:
        r = c.execute("SELECT * FROM checkpoints WHERE job_id=? ORDER BY id DESC LIMIT 1", (jid,)).fetchone()
        if not r: return None
        d = dict(r); d["payload"] = json.loads(d["payload"]); d["budget"] = json.loads(d["budget"])
        return d

def list_checkpoints(jid: str, db_path=None) -> list[dict]:
    with get_conn(db_path) as c:
        return [dict(r) for r in c.execute("SELECT * FROM checkpoints WHERE job_id=? ORDER BY id", (jid,))]

# ---- leases (3.3) ----
LEASE_TTL_S = 120

def acquire_lease(jid: str, owner: str, ttl_s: int = LEASE_TTL_S, db_path=None) -> bool:
    exp = (datetime.now(timezone.utc) + timedelta(seconds=ttl_s)).isoformat()
    with get_conn(db_path) as c:
        # Atomic check-and-claim: BEGIN IMMEDIATE takes the write lock up
        # front so two workers cannot both observe "no live lease" and both
        # claim the same job.
        c.execute("BEGIN IMMEDIATE")
        try:
            r = c.execute("SELECT expires_at FROM leases WHERE job_id=?", (jid,)).fetchone()
            if r and r["expires_at"] > now_iso():
                c.execute("ROLLBACK")
                return False
            c.execute("INSERT OR REPLACE INTO leases(job_id,owner,expires_at) VALUES(?,?,?)", (jid, owner, exp))
            c.execute("UPDATE projection_jobs SET lease_owner=?, lease_expires_at=? WHERE id=?", (owner, exp, jid))
            c.execute("COMMIT")
            return True
        except sqlite3.OperationalError:
            try: c.execute("ROLLBACK")
            except Exception: pass
            raise

def renew_lease(jid: str, owner: str, ttl_s: int = LEASE_TTL_S, db_path=None) -> bool:
    exp = (datetime.now(timezone.utc) + timedelta(seconds=ttl_s)).isoformat()
    def _once():
        with get_conn(db_path) as c:
            r = c.execute("SELECT owner FROM leases WHERE job_id=?", (jid,)).fetchone()
            if not r or r["owner"] != owner: return False
            c.execute("UPDATE leases SET expires_at=? WHERE job_id=?", (exp, jid))
            c.execute("UPDATE projection_jobs SET lease_expires_at=? WHERE id=?", (exp, jid))
            return True
    return _write_retry(_once)

def lease_expired(jid: str, db_path=None) -> bool:
    with get_conn(db_path) as c:
        r = c.execute("SELECT expires_at FROM leases WHERE job_id=?", (jid,)).fetchone()
        return True if not r else r["expires_at"] <= now_iso()

# ---- idempotency (3.4) ----
def idem_key(job: str, phase: str, step: int = 0, particle: str = "", ctx_hash: str = "") -> str:
    raw = f"{job}|{phase}|{step}|{particle}|{ctx_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def idem_get(key: str, db_path=None) -> dict | None:
    with get_conn(db_path) as c:
        r = c.execute("SELECT result FROM idempotency_records WHERE key=?", (key,)).fetchone()
        return json.loads(r["result"]) if r else None

def idem_put(key: str, result: dict, db_path=None) -> None:
    def _once():
        with get_conn(db_path) as c:
            c.execute("INSERT OR IGNORE INTO idempotency_records(key,result) VALUES(?,?)",
                      (key, json.dumps(result)))
    _write_retry(_once)

# ---- budget (3.6) ----
def default_budget() -> dict:
    from app.config import settings
    return {"llm_calls": 0, "tokens": 0, "steps": 0, "branches": 0,
            "caps": {"steps": settings.budget_max_steps, "llm_calls": settings.budget_max_llm_calls,
                     "tokens": settings.budget_max_tokens, "runtime_s": settings.budget_max_runtime_s,
                     "branches": settings.budget_max_branches},
            "started_at": now_iso()}

def check_budget(b: dict) -> tuple[bool, str]:
    c = b.get("caps", {})
    if b.get("steps", 0) >= c.get("steps", 40): return False, "max_steps"
    if b.get("llm_calls", 0) >= c.get("llm_calls", 500): return False, "max_llm_calls"
    if b.get("tokens", 0) >= c.get("tokens", 500000): return False, "max_tokens"
    if b.get("branches", 0) >= c.get("branches", 20): return False, "max_branches"
    return True, ""

def load_budget(jid: str, db_path=None) -> dict:
    job = get_job(jid, db_path)
    try: return json.loads(job["budget_state"]) if job and job["budget_state"] else default_budget()
    except Exception: return default_budget()

def store_budget(jid: str, b: dict, db_path=None) -> None:
    def _once():
        with get_conn(db_path) as c:
            c.execute("UPDATE projection_jobs SET budget_state=? WHERE id=?", (json.dumps(b), jid))
    _write_retry(_once)

# ---- recovery scanner (3.5) ----
def scan_recoverable(db_path=None) -> list[str]:
    out = []
    with get_conn(db_path) as c:
        rows = c.execute("""SELECT id, status, lease_expires_at FROM projection_jobs
          WHERE status IN ('RUNNING','RECOVERING','CHECKPOINTING')""").fetchall()
        for r in rows:
            exp = r["lease_expires_at"]
            if not exp or exp <= now_iso():
                out.append(r["id"])
    for jid in out:
        try: transition(jid, "RECOVERING", "expired lease -> recovering", db_path)
        except Exception: pass
    return out

def scan_queued(db_path=None) -> list[str]:
    """Return ids of jobs waiting to start (status QUEUED). These have not
    yet been picked up by any worker. Distinct from scan_recoverable(), which
    only handles jobs whose lease has expired."""
    with get_conn(db_path) as c:
        return [r["id"] for r in c.execute(
            "SELECT id FROM projection_jobs WHERE status='QUEUED'")]
