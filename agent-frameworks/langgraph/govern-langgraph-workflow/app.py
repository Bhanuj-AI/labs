"""Run the deterministic LangGraph insurance-claim example."""

import json
from pathlib import Path

from governance import EvidenceStatus, ExecutionEvidenceReporter
from workflow import build_workflow


def load_fixture() -> dict[str, object]:
    """Load the local claim fixture used by this example."""
    fixture_path = Path(__file__).parent / "fixtures" / "claim.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def main() -> None:
    claim = load_fixture()
    reporter = ExecutionEvidenceReporter.from_environment()
    reporter.workflow_started(str(claim["claim_id"]))
    result = build_workflow(reporter.wrap_node).invoke({"claim": claim})
    reporter.workflow_completed()

    print(f"Claim: {result['claim_id']}")
    print(f"Policy active: {str(result['policy_active']).lower()}")
    print(f"Damage verified: {str(result['damage_verified']).lower()}")
    print(f"Decision: {result['decision']}")
    if reporter.status is not EvidenceStatus.DISABLED:
        print(f"BHANUJ execution ID: {reporter.execution_id or 'unavailable'}")
        print(f"BHANUJ evidence status: {reporter.status.value}")
        if reporter.error:
            print(f"BHANUJ evidence error: {reporter.error}")


if __name__ == "__main__":
    main()
