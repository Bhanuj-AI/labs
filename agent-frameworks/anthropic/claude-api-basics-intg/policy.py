"""Deterministic, local application tool for the Claude Messages examples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "policies.json"

TOOL = {
    "name": "lookup_policy",
    "description": "Look up the status and coverage limit for one insurance policy.",
    "input_schema": {
        "type": "object",
        "properties": {"policy_id": {"type": "string", "minLength": 1}},
        "required": ["policy_id"],
        "additionalProperties": False,
    },
}


def lookup_policy(policy_id: str) -> dict[str, Any]:
    """Execute the application-owned tool without a network dependency."""
    policies = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    policy = policies.get(policy_id)
    if policy is None:
        return {
            "ok": False,
            "reason_code": "POLICY_NOT_FOUND",
            "content": f"Policy {policy_id} was not found.",
        }
    return {
        "ok": True,
        "reason_code": "POLICY_FOUND",
        "content": (
            f"Policy {policy_id}: status={policy['status']}; "
            f"coverage_limit={policy['coverage_limit']}; "
            f"claim_can_proceed={str(policy['claim_can_proceed']).lower()}."
        ),
    }
