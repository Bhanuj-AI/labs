"""Run the deterministic Google ADK insurance-claim lab."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from governed_workflow import AIGovernanceRuntimeClient, NoOpADKExecutionEvidence
from workflow import run_claim_sync


AGENT_ID = "google-adk-insurance-claim-workflow"


def load_fixture() -> dict[str, object]:
    fixture = Path(__file__).parent / "fixtures" / "claim.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def configured_client() -> AIGovernanceRuntimeClient | None:
    values = {
        "AI_GOVERNANCE_BASE_URL": os.getenv("AI_GOVERNANCE_BASE_URL"),
        "AI_GOVERNANCE_ORGANIZATION_ID": os.getenv("AI_GOVERNANCE_ORGANIZATION_ID"),
        "AI_GOVERNANCE_PROJECT_ID": os.getenv("AI_GOVERNANCE_PROJECT_ID"),
    }
    supplied = {name for name, value in values.items() if value}
    if not supplied:
        return None
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError("AI Governance instrumentation requires " + ", ".join(missing))
    return AIGovernanceRuntimeClient(
        base_url=str(values["AI_GOVERNANCE_BASE_URL"]),
        headers={
            "X-AI-Governance-Organization-Id": str(values["AI_GOVERNANCE_ORGANIZATION_ID"]),
            "X-AI-Governance-Project-Id": str(values["AI_GOVERNANCE_PROJECT_ID"]),
        },
    )


def main() -> None:
    claim = load_fixture()
    invocation_id = f"google-adk-{uuid4()}"
    client = configured_client()
    evidence = (
        client.start_execution(
            agent_id=AGENT_ID,
            agent_name="Google ADK insurance claim workflow",
            agent_version="1.0.0",
            root_invocation_id=invocation_id,
            metadata={"workflow": "insurance_claim"},
        )
        if client
        else NoOpADKExecutionEvidence()
    )

    try:
        result = run_claim_sync(claim, evidence, invocation_id=invocation_id)
    except Exception:
        evidence.complete("FAILED")
        raise
    evidence.complete("SUCCEEDED")

    print(f"Claim: {result['claim_id']}")
    print(f"Policy active: {str(result['policy_active']).lower()}")
    print(f"Damage verified: {str(result['damage_verified']).lower()}")
    print(f"Decision: {result['decision']}")
    if client:
        execution_id = getattr(evidence, "execution_id", None)
        if execution_id:
            print(f"BHANUJ execution ID: {execution_id}")
        else:
            print("BHANUJ evidence delivery: unavailable (workflow result preserved)")


if __name__ == "__main__":
    main()
