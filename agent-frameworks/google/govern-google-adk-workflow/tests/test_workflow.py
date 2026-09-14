from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from governed_workflow import ADKWorkflowStep, AIGovernanceRuntimeClient
from workflow import STAGE_NAMES, run_claim_sync


CLAIM = {
    "claim_id": "CLM-001",
    "customer_id": "CUST-001",
    "claim_amount": 2400,
    "policy_active": True,
    "damage_verified": True,
}


@dataclass
class RecordingEvidence:
    events: list[tuple[str, ADKWorkflowStep]]

    def workflow_step_started(self, step: ADKWorkflowStep | None) -> None:
        assert step is not None
        self.events.append(("STARTED", step))

    def workflow_step_completed(self, step: ADKWorkflowStep | None) -> None:
        assert step is not None
        self.events.append(("COMPLETED", step))

    def workflow_step_failed(self, step: ADKWorkflowStep | None) -> None:
        assert step is not None
        self.events.append(("FAILED", step))

    def complete(self, status: str) -> None:
        del status


class RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def request(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        if kwargs["url"].endswith("/api/v1/agent-executions"):
            return {"execution": {"execution_id": "execution-1"}}
        return {}


def run_with(claim: dict[str, object]) -> tuple[dict[str, object], RecordingEvidence]:
    evidence = RecordingEvidence(events=[])
    result = run_claim_sync(claim, evidence, invocation_id="test-invocation")
    return result, evidence


def test_valid_claim_is_approved_and_emits_eight_workflow_step_pairs() -> None:
    result, evidence = run_with(dict(CLAIM))

    assert result["decision"] == "APPROVED"
    assert [event[0] for event in evidence.events] == [
        lifecycle for _ in STAGE_NAMES for lifecycle in ("STARTED", "COMPLETED")
    ]
    assert [event[1].step_name for event in evidence.events[::2]] == list(STAGE_NAMES)
    assert {event[1].step_id for event in evidence.events[::2]} == {
        event[1].step_id for event in evidence.events[1::2]
    }
    assert {event[1].attributes["adk_node_path"] for event in evidence.events} == {
        f"insurance_claim_workflow@1/{stage}@1" for stage in STAGE_NAMES
    }


def test_inactive_policy_is_rejected() -> None:
    result, _ = run_with({**CLAIM, "policy_active": False})
    assert result["decision"] == "REJECTED"


def test_unverified_damage_is_rejected() -> None:
    result, _ = run_with({**CLAIM, "damage_verified": False})
    assert result["decision"] == "REJECTED"


def test_adk_nodes_emit_only_workflow_step_evidence_with_stable_pair_ids() -> None:
    transport = RecordingTransport()
    client = AIGovernanceRuntimeClient("https://governance.example", {}, transport)
    evidence = client.start_execution(
        agent_id="google-adk-insurance-claim-workflow",
        agent_name="Google ADK insurance claim workflow",
        agent_version="1.0.0",
        root_invocation_id="test-invocation",
    )

    result = run_claim_sync(CLAIM, evidence, invocation_id="test-invocation")
    evidence.complete("SUCCEEDED")

    events = [request["payload"] for request in transport.requests if request["url"].endswith("/events")]
    assert result["decision"] == "APPROVED"
    assert len(events) == 16
    assert {event["event_type"] for event in events} == {"WORKFLOW_STEP"}
    assert {event["source_kind"] for event in events} == {"google_adk.node"}
    assert [event["lifecycle"] for event in events] == [
        lifecycle for _ in STAGE_NAMES for lifecycle in ("STARTED", "COMPLETED")
    ]
    assert [event["step_id"] for event in events[::2]] == [event["step_id"] for event in events[1::2]]
    assert all("TOOL_CALL" not in event.values() for event in events)
