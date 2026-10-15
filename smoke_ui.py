#!/usr/bin/env python3
"""Smoke test the Vataic web UI end-to-end in-process.
Runs the Flask app, creates a job, runs the pipeline via the worker,
then hits every page and verifies HTML renders (no 500s)."""
import importlib
import os
import sys

os.environ["DATABASE_PATH"] = "/tmp/vataic-smoke.db"
os.environ["ARTIFACT_DIR"] = "/tmp/vataic-smoke-artifacts"
os.environ["VATAIC_DB_PATH"] = "/tmp/vataic-smoke.db"
os.environ["VATAIC_ARTIFACT_DIR"] = "/tmp/vataic-smoke-artifacts"
os.environ["VATAIC_LLM_PROVIDER"] = "none"
os.environ["VATAIC_LLM_MODEL"] = "none"
os.environ["VATAIC_LLM_API_KEY"] = ""
os.environ["VATAIC_DATA_PROVIDER"] = "none"
os.environ["VATAIC_DATA_API_KEY"] = ""
for suf in ("", "-wal", "-shm"):
    try:
        os.remove("/tmp/vataic-smoke.db" + suf)
    except Exception:
        pass

import app.config as cfg
importlib.reload(cfg)
from app.migrate import apply_pending
from app.seed import seed
from app import create_app
from worker.workflows import ProjectionJobWorkflow
from app.jobs.repositories import create_job

apply_pending("/tmp/vataic-smoke.db")
seed("/tmp/vataic-smoke.db")

jid = create_job("Vataic smoke: a small coastal city faces a 2030 demand shock from a new data center. What are the plausible macro outcomes?")
print("created job", jid)
out = ProjectionJobWorkflow.run_sync({
    "job_id": jid,
    "premise": "Vataic smoke: a small coastal city faces a 2030 demand shock from a new data center. What are the plausible macro outcomes?",
    "scope": "macro",
    "reality_mode": "grounded",
    "outcome_count": 3,
})
print("run_sync:", out)

app = create_app({"TESTING": True})
cl = app.test_client()
pages = ["/healthz", "/", "/jobs", "/jobs/partial", "/settings",
         "/personal", "/personal/builder", "/personal/safety",
         f"/jobs/{jid}", f"/results/{jid}"]
ok = True
for p in pages:
    r = cl.get(p)
    status = "OK" if r.status_code == 200 else f"FAIL({r.status_code})"
    if r.status_code != 200:
        ok = False
        print(f"{status} {p} -> {r.data[:400].decode(errors='replace')}")
    else:
        print(f"{status} {p}")
print("ALL OK" if ok else "SMOKE FAILED")
sys.exit(0 if ok else 1)
