"""In-process projection worker: polls the DB state machine, no Temporal.

The worker runs a simple polling loop:
  1. On startup and every POLL_INTERVAL seconds, call scan_recoverable()
     to find scenarios with expired leases (RUNNING / RECOVERING / CHECKPOINTING)
     and scan_queued() to find scenarios waiting to start (QUEUED).
  2. For each scenario found, acquire a fresh lease and run the full pipeline
     via ProjectionScenarioWorkflow.run_sync(inp).
  3. A lease-renewal heartbeat thread keeps the lease alive while a long
     scenario is in flight, so the recovery scanner doesn't steal it.

The DB state machine (states.py), checkpoints, idempotency records, and
leases already provide all the durable-execution semantics that Temporal
would add. Removing Temporal eliminates the dominant CPU consumer on the
devbox's single core.
"""
import logging
import threading
import time

from app.scenarios.repos import (
    acquire_lease,
    get_scenario,
    renew_lease,
    scan_queued,
    scan_recoverable,
)
from worker.workflows import ProjectionScenarioWorkflow

logger = logging.getLogger(__name__)

# How often to scan for recoverable scenarios (seconds).
# The projection loop already checkpoints per step, so a shorter interval
# just means faster recovery after a crash.
POLL_INTERVAL = 5  # seconds

# Lease TTL in seconds. Matches the default in app/scenarios/repositories.py.
# A scenario holding a lease is renewed every TTL/3 while running; if a worker
# dies for longer than TTL, the recovery scanner reclaims it.
LEASE_TTL_S = 300


class LeaseRenewalThread(threading.Thread):
    """Background thread that renews the lease for a scenario while it runs."""

    def __init__(self, scenario_id: str, owner: str, ttl_s: int):
        super().__init__(daemon=True)
        self.scenario_id = scenario_id
        self.owner = owner
        self.ttl_s = ttl_s
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                renewed = renew_lease(self.scenario_id, self.owner, self.ttl_s)
                if renewed:
                    logger.debug("lease renewed for scenario %s", self.scenario_id)
                else:
                    logger.warning("lease renewal failed for scenario %s", self.scenario_id)
            except Exception as e:
                logger.warning("lease renewal error for scenario %s: %s", self.scenario_id, e)
            time.sleep(max(1, self.ttl_s // 3))

    def stop(self):
        self._stop.set()


def _build_input(scenario_id: str) -> dict:
    """Build the input dict for run_sync from the scenario row."""
    j = get_scenario(scenario_id)
    if not j:
        return {}
    return {
        "scenario_id": scenario_id,
        "premise": j.get("premise", ""),
        "scope": j.get("scope", "macro"),
        "reality_mode": j.get("reality_mode", "grounded"),
        "horizon": j.get("horizon", "auto"),
        "outcome_count": int(j.get("outcome_count", 7)),
    }


def _run_scenario(scenario_id: str, owner: str) -> None:
    """Acquire lease for a scenario and run the full pipeline via run_sync."""
    logger.info("starting scenario %s (owner=%s)", scenario_id, owner)
    inp = _build_input(scenario_id)
    if not inp:
        logger.warning("no input for scenario %s, skipping", scenario_id)
        return

    # Acquire the lease. If it fails, another worker already has it.
    # A transient lock error must not be read as "another worker owns this":
    # retry a few times before giving up.
    leased = False
    for _attempt in range(3):
        try:
            leased = acquire_lease(scenario_id, owner, ttl_s=LEASE_TTL_S, db_path=None)
            break
        except Exception as e:
            if "database is locked" in str(e) and _attempt < 2:
                time.sleep(1.0 * (_attempt + 1))
                continue
            raise
    if not leased:
        logger.warning("could not acquire lease for scenario %s, skipping", scenario_id)
        return

    # Start the lease-renewal heartbeat so the scanner doesn't steal us.
    renewal = LeaseRenewalThread(scenario_id, owner, LEASE_TTL_S)
    renewal.start()
    try:
        out = ProjectionScenarioWorkflow.run_sync(inp)
        logger.info("scenario %s finished: %s", scenario_id, out.get("status"))
    except Exception as e:
        logger.exception("scenario %s raised: %s", scenario_id, e)
        # run_sync transitions the scenario to FAILED on error, but make sure
        # the lease is released so recovery can pick it up again.
        try:
            acquire_lease(scenario_id, "dead", ttl_s=-1)
        except Exception:
            pass
    finally:
        renewal.stop()
        renewal.join(timeout=5)


def _scan_and_run(owner: str) -> None:
    """Scan for recoverable and queued scenarios and run each one."""
    recoverable = []
    try:
        recoverable = scan_recoverable()
    except Exception as e:
        logger.error("scan_recoverable failed: %s", e)
    queued = []
    try:
        queued = scan_queued()
    except Exception as e:
        logger.error("scan_queued failed: %s", e)

    scenarios = list(recoverable) + [q for q in queued if q not in recoverable]
    if not scenarios:
        return

    logger.info("scan found %d scenario(s) to run: %s", len(scenarios), scenarios)
    for scenario_id in scenarios:
        j = get_scenario(scenario_id)
        if not j:
            continue
        status = j.get("status")
        if status not in ("RUNNING", "RECOVERING", "CHECKPOINTING", "QUEUED"):
            continue
        _run_scenario(scenario_id, owner)
        # _run_scenario drives a scenario end-to-end (or leaves it RUNNING with a live
        # lease after a crash). Either way the state changed underneath this
        # scan snapshot: re-derive the remaining list on the next poll so we
        # never re-enter a scenario that is already in progress.
        break


def main() -> None:
    """Entry point: start the polling worker."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    owner = f"worker-{int(time.time())}"
    logger.info("vataic worker starting (owner=%s, poll_interval=%ds)", owner, POLL_INTERVAL)

    # Initial scan to pick up any scenarios left from a previous run.
    _scan_and_run(owner)

    while True:
        time.sleep(POLL_INTERVAL)
        try:
            _scan_and_run(owner)
        except Exception as e:
            logger.exception("poll loop error: %s", e)


if __name__ == "__main__":
    main()
