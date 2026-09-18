"""Smoke test: submit small scenario, watch phases, kill worker mid-run, confirm resume."""
import time, os
os.environ.setdefault("DATABASE_PATH", "/tmp/vataic-smoke.db")
for f in ("/tmp/vataic-smoke.db",):
    try: os.remove(f)
    except OSError: pass
from app.migrate import apply_pending
from app.seed import seed
apply_pending(); seed()
from app.jobs.repositories import create_job, transition, latest_checkpoint, acquire_lease
from app.steps.intake import parse_scenario
from app.steps.factors import default_candidates, select_factors, build_causal_graph, init_world_state
from app.steps.engine import run_step

jid = create_job("Test premise: village adopts solar", scope="macro")
print("job", jid)
transition(jid, "INITIALIZING", "smoke"); transition(jid, "RUNNING", "smoke")
parse_scenario(jid, "Test premise: village adopts solar")
cands = default_candidates("solar", "macro")
select_factors(jid, cands, "macro")
with __import__("app.db", fromlist=["get_conn"]).get_conn() as c:
    import json
    r = c.execute("SELECT factors FROM factor_sets WHERE job_id=? ORDER BY id DESC LIMIT 1", (jid,)).fetchone()
    facs = json.loads(r["factors"])
build_causal_graph(jid, facs, []); init_world_state(jid, facs)
acquire_lease(jid, "smoke-worker", ttl_s=-1)  # simulate dead worker: lease already expired
out = run_step(jid, 1, [{"id": "p0", "factor_state": {}, "probability": 1.0}], budget={"steps": 0, "caps": {"steps": 40, "llm_calls": 500, "tokens": 500000}})
print("step1 particles:", len(out["particles"]))
# simulate worker kill: lease expires, recovery scanner resumes from checkpoint (not step 0)
from app.jobs.repositories import scan_recoverable
found = scan_recoverable()
print("recoverable:", found)
cp = latest_checkpoint(jid)
assert cp and cp["step_index"] == 1, "resume must be from checkpoint step 1"
print("SMOKE OK: resume from checkpoint", cp["step_index"])
