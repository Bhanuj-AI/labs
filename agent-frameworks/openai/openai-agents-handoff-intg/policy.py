"""Local policy-fixture access; this module has no governance dependency."""

from __future__ import annotations

import json
from pathlib import Path


def lookup_policy_record(policy_number: str) -> dict[str, object]:
    """Return one deterministic local policy record."""
    fixture = Path(__file__).parent / "fixtures" / "policies.json"
    policies = json.loads(fixture.read_text(encoding="utf-8"))
    policy = policies.get(policy_number)
    if not isinstance(policy, dict):
        return {"policy_number": policy_number, "status": "NOT_FOUND"}
    return policy
