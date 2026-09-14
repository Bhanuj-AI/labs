from __future__ import annotations

from typing import Any, Mapping

from governance import (
    AgentsRuntimeClient,
    EvidenceStatus,
    ExecutionEvidenceReporter,
    GovernanceDeliveryError,
    GovernanceSettings,
)
from workflow import build_workflow


CLAIM = {
    "claim_id": "CLM-001",
    "customer_id": "CUST-001",
    "claim_amount": 2400,
    "policy_active": True,
    "damage_verified": True,
}


class RecordingTransport:
    def __init__(self, fail_after: int | None = None) -> None:
        self.requests: list[tuple[str, Mapping[str, str], dict[str, Any]]] = []
        self.fail_after = fail_after

    def __call__(
        self, url: str, headers: Mapping[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.requests.append((url, headers, payload))
        if self.fail_after is not None and len(self.requests) > self.fail_after:
            raise GovernanceDeliveryError("AI Governance Control Plane is unavailable.")
        if url.endswith("/agent-executions"):
            return {"execution": {"execution_id": "platform-execution-001"}}
        return {}


def run_instrumented_claim(transport: RecordingTransport):
    client = AgentsRuntimeClient(
        GovernanceSettings("http://governance.test", "org_default", "project_default"),
        transport=transport,
    )
    reporter = ExecutionEvidenceReporter(client, external_execution_id="langgraph-run-001")
    reporter.workflow_started(CLAIM["claim_id"])
    result = build_workflow(reporter.wrap_node).invoke({"claim": CLAIM})
    reporter.workflow_completed()
    return result, reporter


def test_instrumented_execution_preserves_the_business_decision() -> None:
    result, reporter = run_instrumented_claim(RecordingTransport())

    assert result["decision"] == "APPROVED"
    assert reporter.execution_id == "platform-execution-001"
    assert reporter.external_execution_id == "langgraph-run-001"
    assert reporter.status is EvidenceStatus.SUCCEEDED


def test_instrumented_execution_emits_one_correlated_workflow_step_timeline() -> None:
    transport = RecordingTransport()
    _, reporter = run_instrumented_claim(transport)

    assert transport.requests[0][0] == "http://governance.test/api/v1/agent-executions"
    assert transport.requests[0][2]["external_execution_id"] == "langgraph-run-001"
    assert transport.requests[0][1]["X-AI-Governance-Organization-Id"] == "org_default"
    assert transport.requests[0][1]["X-AI-Governance-Project-Id"] == "project_default"

    events = transport.requests[1:-1]
    assert len(events) == 8
    assert [event[2]["step_name"] for event in events] == [
        "load_claim",
        "load_claim",
        "check_policy",
        "check_policy",
        "evaluate_evidence",
        "evaluate_evidence",
        "make_decision",
        "make_decision",
    ]
    assert [event[2]["lifecycle"] for event in events] == [
        "STARTED",
        "COMPLETED",
    ] * 4
    assert all(event[2]["event_type"] == "WORKFLOW_STEP" for event in events)
    assert all(event[2]["source_kind"] == "langgraph.node" for event in events)
    assert all(
        event[2]["step_id"]
        == f"{reporter.execution_id}:node:{event[2]['step_name']}"
        for event in events
    )
    assert not [
        event for event in events if event[2]["event_type"] == "TOOL_CALL"
    ]
    assert all(
        event[0]
        == f"http://governance.test/api/v1/agent-executions/{reporter.execution_id}/events"
        for event in events
    )
    assert events[-1][2]["attributes"]["decision"] == "APPROVED"
    assert transport.requests[-1][0] == (
        f"http://governance.test/api/v1/agent-executions/{reporter.execution_id}/complete"
    )
    assert transport.requests[-1][2] == {"status": "SUCCEEDED"}


def test_governance_delivery_failure_does_not_change_the_business_decision() -> None:
    result, reporter = run_instrumented_claim(RecordingTransport(fail_after=1))

    assert result["decision"] == "APPROVED"
    assert reporter.execution_id == "platform-execution-001"
    assert reporter.status is EvidenceStatus.DEGRADED
    assert reporter.error == "AI Governance Control Plane is unavailable."
