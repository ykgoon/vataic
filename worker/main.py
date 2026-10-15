"""In-process projection worker: polls the DB state machine, no Temporal.

The worker runs a simple polling loop:
  1. On startup and every POLL_INTERVAL seconds, call scan_recoverable()
     to find jobs with expired leases (RUNNING / RECOVERING / CHECKPOINTING).
  2. For each job found, acquire a fresh lease and run the full pipeline
     via ProjectionJobWorkflow.run_sync(inp).
  3. A lease-renewal heartbeat thread keeps the lease alive while a long
     job is in flight, so the recovery scanner doesn't steal it.

The DB state machine (states.py), checkpoints, idempotency records, and
leases already provide all the durable-execution semantics that Temporal
would add. Removing Temporal eliminates the dominant CPU consumer on the
devbox's single core.
"""
import logging
import threading
import time

from app.jobs.repositories import (
    acquire_lease,
    get_job,
    renew_lease,
    scan_recoverable,
)
from worker.workflows import ProjectionJobWorkflow

logger = logging.getLogger(__name__)

# How often to scan for recoverable jobs (seconds).
# The projection loop already checkpoints per step, so a shorter interval
# just means faster recovery after a crash.
POLL_INTERVAL = 5  # seconds

# Lease TTL in seconds. Matches the default in app/jobs/repositories.py.
# A job holding a lease is renewed every TTL/3 while running; if a worker
# dies for longer than TTL, the recovery scanner reclaims it.
LEASE_TTL_S = 300


class LeaseRenewalThread(threading.Thread):
    """Background thread that renews the lease for a job while it runs."""

    def __init__(self, job_id: str, owner: str, ttl_s: int):
        super().__init__(daemon=True)
        self.job_id = job_id
        self.owner = owner
        self.ttl_s = ttl_s
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                renewed = renew_lease(self.job_id, self.owner, self.ttl_s)
                if renewed:
                    logger.debug("lease renewed for job %s", self.job_id)
                else:
                    logger.warning("lease renewal failed for job %s", self.job_id)
            except Exception as e:
                logger.warning("lease renewal error for job %s: %s", self.job_id, e)
            time.sleep(max(1, self.ttl_s // 3))

    def stop(self):
        self._stop.set()


def _build_input(job_id: str) -> dict:
    """Build the input dict for run_sync from the job row."""
    j = get_job(job_id)
    if not j:
        return {}
    return {
        "job_id": job_id,
        "premise": j.get("premise", ""),
        "scope": j.get("scope", "macro"),
        "reality_mode": j.get("reality_mode", "grounded"),
        "horizon": j.get("horizon", "auto"),
        "outcome_count": int(j.get("outcome_count", 7)),
    }


def _run_job(job_id: str, owner: str) -> None:
    """Acquire lease for a job and run the full pipeline via run_sync."""
    logger.info("starting job %s (owner=%s)", job_id, owner)
    inp = _build_input(job_id)
    if not inp:
        logger.warning("no input for job %s, skipping", job_id)
        return

    # Acquire the lease. If it fails, another worker already has it.
    if not acquire_lease(job_id, owner, ttl_s=LEASE_TTL_S, db_path=None):
        logger.warning("could not acquire lease for job %s, skipping", job_id)
        return

    # Start the lease-renewal heartbeat so the scanner doesn't steal us.
    renewal = LeaseRenewalThread(job_id, owner, LEASE_TTL_S)
    renewal.start()
    try:
        out = ProjectionJobWorkflow.run_sync(inp)
        logger.info("job %s finished: %s", job_id, out.get("status"))
    except Exception as e:
        logger.exception("job %s raised: %s", job_id, e)
        # run_sync transitions the job to FAILED on error, but make sure
        # the lease is released so recovery can pick it up again.
        try:
            acquire_lease(job_id, "dead", ttl_s=-1)
        except Exception:
            pass
    finally:
        renewal.stop()
        renewal.join(timeout=5)


def _scan_and_run(owner: str) -> None:
    """Scan for recoverable jobs and run each one."""
    try:
        recoverable = scan_recoverable()
    except Exception as e:
        logger.error("scan_recoverable failed: %s", e)
        return

    if not recoverable:
        return

    logger.info("scan found %d recoverable job(s): %s", len(recoverable), recoverable)
    for job_id in recoverable:
        j = get_job(job_id)
        if not j:
            continue
        status = j.get("status")
        if status not in ("RUNNING", "RECOVERING", "CHECKPOINTING", "QUEUED"):
            continue
        _run_job(job_id, owner)


def main() -> None:
    """Entry point: start the polling worker."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    owner = f"worker-{int(time.time())}"
    logger.info("vataic worker starting (owner=%s, poll_interval=%ds)", owner, POLL_INTERVAL)

    # Initial scan to pick up any jobs left from a previous run.
    _scan_and_run(owner)

    while True:
        time.sleep(POLL_INTERVAL)
        try:
            _scan_and_run(owner)
        except Exception as e:
            logger.exception("poll loop error: %s", e)


if __name__ == "__main__":
    main()
