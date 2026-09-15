"""The local deterministic policy tool used by this tutorial."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "policies.json"


def load_policy(policy_id: str) -> dict[str, Any] | None:
    """Read the tutorial fixture; no network or external service is involved."""
    policies = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return policies.get(policy_id)


@tool(
    "lookup_policy",
    "Look up the status and coverage limit for one insurance policy.",
    {"policy_id": str},
)
async def lookup_policy(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic result in the Claude SDK in-process MCP format."""
    policy_id = str(arguments["policy_id"])
    policy = load_policy(policy_id)
    if policy is None:
        text = f"Policy {policy_id} was not found."
    else:
        text = (
            f"Policy {policy_id}: status={policy['status']}; "
            f"coverage_limit={policy['coverage_limit']}; "
            f"claim_can_proceed={str(policy['claim_can_proceed']).lower()}."
        )
    return {"content": [{"type": "text", "text": text}]}
