from __future__ import annotations

import asyncio
from typing import Any

from agents import Runner  # type: ignore
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call  # type: ignore

from agent import HANDOFF_TOOL_NAME, POLICY_AGENT_NAME, TRIAGE_AGENT_NAME, create_claims_agents
from governance import (
    AgentsRuntimeClient,
    EvidenceStatus,
    ExecutionEvidenceReporter,
    GovernanceSettings,
    GovernedRunHooks,
)
from policy import lookup_policy_record


class RecordingTransport:
    def __init__(self, fail_events: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self.fail_events = fail_events

    def post(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append({"url": url, "headers": headers, "payload": payload})
        if url.endswith("/agent-executions"):
            return {"execution": {"execution_id": "execution-123"}}
        if self.fail_events and url.endswith("/events"):
            raise RuntimeError("evidence unavailable")
        return {}


def reporter_with(transport: RecordingTransport) -> ExecutionEvidenceReporter:
    settings = GovernanceSettings("https://governance.example", "org-a", "project-a")
    reporter = ExecutionEvidenceReporter(
        AgentsRuntimeClient(settings, transport), external_execution_id="openai-handoff-test"
    )
    reporter.begin()
    return reporter


def successful_steps() -> list[list[object]]:
    return [
        [function_call(HANDOFF_TOOL_NAME, {}, call_id="handoff-1")],
        [function_call("lookup_policy", {"policy_number": "POL-100"}, call_id="tool-1")],
        [assistant_message("Policy POL-100: ACTIVE.")],
    ]


def run_script(reporter: ExecutionEvidenceReporter, steps: list[object]) -> str:
    model = ScriptedModel(steps)
    triage_agent, _ = create_claims_agents(model)
    result = asyncio.run(
        Runner.run(triage_agent, "Validate policy POL-100.", hooks=GovernedRunHooks(reporter))
    )
    model.assert_complete()
    return str(result.final_output)


def event_payloads(transport: RecordingTransport) -> list[dict[str, Any]]:
    return [request["payload"] for request in transport.requests if request["url"].endswith("/events")]


def test_native_handoff_emits_the_observed_agent_transition_timeline() -> None:
    transport = RecordingTransport()
    reporter = reporter_with(transport)

    assert run_script(reporter, successful_steps()) == "Policy POL-100: ACTIVE."
    reporter.complete("SUCCEEDED")

    events = event_payloads(transport)
    assert [(event["event_type"], event.get("lifecycle")) for event in events] == [
        ("WORKFLOW_STEP", "STARTED"),
        ("MODEL_CALL", None),
        ("WORKFLOW_STEP", "COMPLETED"),
        ("WORKFLOW_STEP", "STARTED"),
        ("MODEL_CALL", None),
        ("TOOL_CALL", None),
        ("MODEL_CALL", None),
        ("WORKFLOW_STEP", "COMPLETED"),
    ]
    assert reporter.status is EvidenceStatus.SUCCEEDED
    assert sum(event["event_type"] == "WORKFLOW_STEP" for event in events) == 4
    assert sum(event["event_type"] == "MODEL_CALL" for event in events) == 3
    assert sum(event["event_type"] == "TOOL_CALL" for event in events) == 1
    assert all("parent_step_id" not in event for event in events)
    assert events[0]["step_name"] == TRIAGE_AGENT_NAME
    assert events[2]["attributes"] == {
        "status": "COMPLETED",
        "transition_type": "HANDOFF",
        "handoff_from_agent": TRIAGE_AGENT_NAME,
        "handoff_to_agent": POLICY_AGENT_NAME,
    }
    assert events[3]["attributes"] == {
        "status": "STARTED",
        "transition_type": "HANDOFF",
        "handoff_from_agent": TRIAGE_AGENT_NAME,
        "handoff_to_agent": POLICY_AGENT_NAME,
    }
    assert [event["attributes"]["agent_name"] for event in events if event["event_type"] == "MODEL_CALL"] == [
        TRIAGE_AGENT_NAME,
        POLICY_AGENT_NAME,
        POLICY_AGENT_NAME,
    ]
    tool_events = [event for event in events if event["event_type"] == "TOOL_CALL"]
    assert len(tool_events) == 1
    assert tool_events[0]["actor_id"] == "lookup_policy"
    assert HANDOFF_TOOL_NAME not in str(tool_events)
    assert transport.requests[-1]["payload"] == {"status": "SUCCEEDED"}


def test_each_agent_activation_has_a_distinct_stable_step_identity() -> None:
    transport = RecordingTransport()
    reporter = reporter_with(transport)
    model = ScriptedModel([])
    triage_agent, _ = create_claims_agents(model)

    reporter.agent_started(triage_agent)
    reporter.agent_completed(triage_agent)
    reporter.agent_started(triage_agent)
    reporter.agent_completed(triage_agent)

    steps = event_payloads(transport)
    assert steps[0]["step_id"] == steps[1]["step_id"]
    assert steps[2]["step_id"] == steps[3]["step_id"]
    assert steps[0]["step_id"] != steps[2]["step_id"]


def test_failure_before_handoff_fails_only_the_triage_activation() -> None:
    transport = RecordingTransport()
    reporter = reporter_with(transport)

    try:
        run_script(reporter, [ModelStep.raise_error(RuntimeError("model unavailable"))])
    except RuntimeError:
        reporter.fail_active_agent()
        reporter.complete("FAILED")
    else:
        raise AssertionError("The scripted model error must be preserved.")

    steps = event_payloads(transport)
    assert [(step["step_name"], step["lifecycle"]) for step in steps if step["event_type"] == "WORKFLOW_STEP"] == [
        (TRIAGE_AGENT_NAME, "STARTED"),
        (TRIAGE_AGENT_NAME, "FAILED"),
    ]
    assert transport.requests[-1]["payload"] == {"status": "FAILED"}


def test_failure_after_handoff_preserves_triage_completion_and_fails_specialist() -> None:
    transport = RecordingTransport()
    reporter = reporter_with(transport)

    try:
        run_script(
            reporter,
            [
                [function_call(HANDOFF_TOOL_NAME, {}, call_id="handoff-1")],
                ModelStep.raise_error(RuntimeError("model unavailable")),
            ],
        )
    except RuntimeError:
        reporter.fail_active_agent()
        reporter.complete("FAILED")
    else:
        raise AssertionError("The scripted model error must be preserved.")

    steps = event_payloads(transport)
    assert [(step["step_name"], step["lifecycle"]) for step in steps if step["event_type"] == "WORKFLOW_STEP"] == [
        (TRIAGE_AGENT_NAME, "STARTED"),
        (TRIAGE_AGENT_NAME, "COMPLETED"),
        (POLICY_AGENT_NAME, "STARTED"),
        (POLICY_AGENT_NAME, "FAILED"),
    ]
    assert transport.requests[-1]["payload"] == {"status": "FAILED"}


def test_evidence_delivery_failure_is_fail_open_for_local_policy_logic() -> None:
    transport = RecordingTransport(fail_events=True)
    reporter = reporter_with(transport)
    model = ScriptedModel([])
    triage_agent, _ = create_claims_agents(model)

    reporter.agent_started(triage_agent)
    assert reporter.status is EvidenceStatus.DEGRADED
    assert lookup_policy_record("POL-100")["status"] == "ACTIVE"
    assert lookup_policy_record("POL-200")["status"] == "INACTIVE"
