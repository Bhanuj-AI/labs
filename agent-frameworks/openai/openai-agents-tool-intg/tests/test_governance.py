from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from agent import create_policy_validation_agent
from agents.items import ModelResponse
from agents.usage import Usage
from governance import (
    AgentsRuntimeClient,
    EvidenceStatus,
    ExecutionEvidenceReporter,
    GovernanceSettings,
    GovernedRunHooks,
)
from policy import lookup_policy_record


class RecordingTransport:
    def __init__(self, fail_after_start: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self.fail_after_start = fail_after_start

    def post(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append({"url": url, "headers": headers, "payload": payload})
        if url.endswith("/agent-executions"):
            return {"execution": {"execution_id": "execution-123"}}
        if self.fail_after_start:
            raise RuntimeError("offline")
        return {}


def reporter_with(transport: RecordingTransport) -> ExecutionEvidenceReporter:
    settings = GovernanceSettings("https://governance.example", "org-a", "project-a")
    reporter = ExecutionEvidenceReporter(AgentsRuntimeClient(settings, transport), external_execution_id="openai-agents-test-run")
    reporter.begin()
    return reporter


async def record_success(reporter: ExecutionEvidenceReporter) -> None:
    hooks = GovernedRunHooks(reporter)
    agent = create_policy_validation_agent("gpt-5-mini")
    response = ModelResponse(
        output=[],
        usage=Usage(input_tokens=11, output_tokens=7, total_tokens=18),
        response_id="response-1",
    )
    context = SimpleNamespace(tool_call_id="call-policy-1")
    tool = SimpleNamespace(name="lookup_policy")

    await hooks.on_agent_start(SimpleNamespace(), agent)
    await hooks.on_llm_start(SimpleNamespace(), agent, "do not capture this", [{"secret": "no"}])
    await hooks.on_llm_end(SimpleNamespace(), agent, response)
    await hooks.on_tool_start(context, agent, tool)
    await hooks.on_tool_end(context, agent, tool, "ACTIVE")
    await hooks.on_llm_start(SimpleNamespace(), agent, "do not capture this either", [])
    await hooks.on_llm_end(SimpleNamespace(), agent, response)
    await hooks.on_agent_end(SimpleNamespace(), agent, "Policy POL-100: ACTIVE.")


def event_payloads(transport: RecordingTransport) -> list[dict[str, Any]]:
    return [request["payload"] for request in transport.requests if request["url"].endswith("/events")]


def test_successful_execution_emits_the_expected_public_runtime_timeline() -> None:
    transport = RecordingTransport()
    reporter = reporter_with(transport)

    asyncio.run(record_success(reporter))
    reporter.complete("SUCCEEDED")

    events = event_payloads(transport)
    assert reporter.execution_id == "execution-123"
    assert reporter.status is EvidenceStatus.SUCCEEDED
    assert [(event["event_type"], event.get("lifecycle")) for event in events] == [
        ("WORKFLOW_STEP", "STARTED"),
        ("MODEL_CALL", None),
        ("TOOL_CALL", None),
        ("MODEL_CALL", None),
        ("WORKFLOW_STEP", "COMPLETED"),
    ]
    assert events[0]["source_kind"] == "openai_agents.agent"
    assert events[2]["actor_type"] == "TOOL"
    assert events[2]["actor_id"] == "lookup_policy"
    assert events[2]["attributes"] == {
        "status": "COMPLETED",
        "latency_ms": events[2]["attributes"]["latency_ms"],
        "tool_call_id": "call-policy-1",
    }
    assert events[1]["attributes"].items() >= {
        "model": "gpt-5-mini",
        "ordinal": 1,
        "status": "COMPLETED",
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }.items()
    assert transport.requests[-1]["payload"] == {"status": "SUCCEEDED"}
    rendered = str(events)
    assert "do not capture" not in rendered
    assert "secret" not in rendered
    assert "ACTIVE" not in rendered


def test_step_correlation_is_stable_within_one_execution() -> None:
    transport = RecordingTransport()
    reporter = reporter_with(transport)
    asyncio.run(record_success(reporter))

    events = event_payloads(transport)
    assert events[0]["step_id"] == events[-1]["step_id"]
    assert events[0]["idempotency_key"].endswith(":STARTED")
    assert events[-1]["idempotency_key"].endswith(":COMPLETED")


def test_evidence_failure_is_fail_open_and_does_not_change_policy_logic() -> None:
    transport = RecordingTransport(fail_after_start=True)
    reporter = reporter_with(transport)
    hooks = GovernedRunHooks(reporter)
    agent = create_policy_validation_agent("gpt-5-mini")

    asyncio.run(hooks.on_agent_start(SimpleNamespace(), agent))
    assert reporter.status is EvidenceStatus.DEGRADED
    assert lookup_policy_record("POL-100")["status"] == "ACTIVE"
    assert lookup_policy_record("POL-200")["status"] == "INACTIVE"


def test_named_tool_choice_is_reset_by_the_sdk_after_the_first_tool_turn() -> None:
    agent = create_policy_validation_agent("gpt-5-mini")
    assert agent.model_settings.tool_choice == "lookup_policy"
    assert agent.reset_tool_choice is True
