"""LLM-driven backcast: given a completed scenario, use the LLM to work
backwards from each outcome and identify what decisions, choices, or
actions would have led to it.

This implements the "backcast-to-decisions" phase from the world-projecting
skill, adapted for Vataic's DB-driven web app form factor.
"""
import json
import re
import logging

from app.llm.client import LLMClient
from app.scenarios.repos import get_scenario

logger = logging.getLogger(__name__)

BACKCAST_PROMPT = """
You are a strategic backcasting analyst. You are given a premise and its
projected outcomes (each with a probability band and a short description).

For EACH outcome, identify 2-4 concrete decisions, choices, or actions that
would need to be made in the near-term (next 12-24 months) to make that
outcome materialize. These are the "decisions that lead to this future."

Format your response as a JSON object with one key per outcome name. Each
value is a list of decision strings.

Example:
{
  "outcome_a_name": [
    "Decision 1 that would lead to this future",
    "Decision 2 that would lead to this future"
  ],
  "outcome_b_name": [
    "Decision 1 that would lead to this future"
  ]
}

Be specific and actionable. Frame each decision as something a decision-maker
could actually do. Consider the premise context.
"""


def backcast_to_decisions(scenario_id: str, client: LLMClient = None) -> dict:
    """Run LLM-driven backcast for a completed scenario.

    Returns a dict mapping outcome_name -> list of decision strings.
    """
    scenario = get_scenario(scenario_id)
    if not scenario:
        raise ValueError(f"Scenario {scenario_id} not found")
    if scenario.get("status") != "COMPLETED":
        raise ValueError(f"Scenario {scenario_id} is not completed")

    # Load outcomes
    from app.db import get_conn
    with get_conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM outcomes WHERE scenario_id=? ORDER BY id", (scenario_id,))]
    if not rows:
        return {}

    # Build prompt context
    premise = scenario.get("premise", "")
    outcomes_text = "\n".join(
        f"- {r['name']} (band: {r.get('band', 'n/a')})"
        for r in rows
    )

    prompt = (
        BACKCAST_PROMPT
        + f"\nPremise: {premise}\n\n"
        + f"Outcomes:\n{outcomes_text}"
    )

    if client is None:
        client = LLMClient()

    try:
        response = client.complete(prompt, max_tokens=3000)
        raw = response.get("content", response.get("text", ""))
    except Exception as e:
        logger.error("Backcast LLM call failed: %s", e)
        return {}

    # Parse JSON from response
    try:
        # Extract JSON from response
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            raw = match.group(0)
        else:
            raw_stripped = raw.strip()
            if raw_stripped.startswith("{"):
                raw = raw_stripped
            else:
                start = raw.find("{")
                end = raw.rfind("}")
                if start >= 0 and end > start:
                    raw = raw[start:end+1]
                else:
                    return {}
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Backcast JSON parse failed: %s", e)
        return {}

    return result


def save_backcast(scenario_id: str, result: dict):
    """Store backcast results in the database."""
    from app.db import get_conn
    with get_conn() as c:
        c.execute(
            "UPDATE projection_scenarios SET backcast=? WHERE id=?",
            (json.dumps(result), scenario_id)
        )
        c.commit()
