from __future__ import annotations

from types import SimpleNamespace

import pytest
from bhanuj_governance_plugin_api import (
    ReplayExecutionContext,
    ReplayInterventionEnvelope,
)
from openai_agent_runtime.contracts import (
    REPLAY_INTERVENTION_ENVELOPE_SCHEMA_VERSION,
)
from openai_agent_runtime.core_replay_adapter import (
    OpenAIAgentRuntimeReplayAdapter,
    OpenAIAgentRuntimeReplayError,
    ReplayHttpResponse,
)
from openai_agent_runtime.plugin import OpenAIAgentRuntimePlugin
from openai_agent_runtime.runtime import LOW_RISK_COUNTERFACTUAL

ENDPOINT = "https://runtime.example.test/replay"
REPLAY_REFERENCE = "openai-agent-runtime://replays/1a2b3c4d"


class _Transport:
    def __init__(self, response: ReplayHttpResponse) -> None:
        self.response = response
        self.requests = []

    def post(self, endpoint, payload, headers, timeout_seconds):
        self.requests.append((endpoint, payload, headers, timeout_seconds))
        return self.response


class _NotCancelled:
    is_cancelled = False


def test_core_adapter_passes_only_the_governed_envelope_and_rejects_digest_mismatch() -> (
    None
):
    transport = _Transport(_response())
    adapter = OpenAIAgentRuntimeReplayAdapter(
        approved_endpoint=ENDPOINT,
        transport=transport,
        token_provider=lambda: "runtime-token",
    )

    replay = adapter.replay(_source(), _configuration(), _context())

    request = transport.requests[0]
    assert request[0] == ENDPOINT
    assert request[2]["Authorization"] == "Bearer runtime-token"
    assert set(request[1]) == {"replay_reference", "envelope"}
    assert set(request[1]["envelope"]) == {
        "schema_version",
        "policy_id",
        "policy_version",
        "external_execution_id",
        "runtime_tool_call_id",
        "intervention_provider",
        "intervention_provider_version",
        "strategy",
        "original_evidence_digest",
        "counterfactual_reference",
        "counterfactual_digest",
        "intervention_digest",
    }
    assert (
        request[1]["envelope"]["schema_version"]
        == REPLAY_INTERVENTION_ENVELOPE_SCHEMA_VERSION
    )
    assert "risk_level" not in str(request[1])
    assert "confidence" not in str(request[1])
    assert replay.final_state["causal_audit_outcome_score"] == 0.0
    assert (
        replay.final_state["counterfactual_evidence_digest"]
        == LOW_RISK_COUNTERFACTUAL.digest
    )

    bad = _response()
    bad.payload["counterfactual_evidence_digest"] = "sha256:" + ("0" * 64)
    adapter = OpenAIAgentRuntimeReplayAdapter(
        approved_endpoint=ENDPOINT,
        transport=_Transport(bad),
        token_provider=lambda: None,
    )
    with pytest.raises(OpenAIAgentRuntimeReplayError, match="unsafe or malformed"):
        adapter.replay(_source(), _configuration(), _context())


def test_plugin_registers_the_versioned_adapter_through_the_plugin_api() -> None:
    registered = []

    class _Contributions:
        def replay_execution_adapters(self, items) -> None:
            registered.extend(items)

    OpenAIAgentRuntimePlugin().register(SimpleNamespace(contributions=_Contributions()))

    assert [item.name for item in registered] == ["openai-agent-runtime/v1"]


def _source() -> object:
    return SimpleNamespace(
        artifact_refs=(),
        runtime_parameters={
            "agent_runtime_replay": {
                "runtime_type": "openai-agent-runtime",
                "adapter_id": "openai-agent-runtime/v1",
                "adapter_version": "v1",
                "replay_reference": REPLAY_REFERENCE,
                "endpoint": ENDPOINT,
                "supported_interventions": ["REPLACE"],
            }
        },
    )


def _configuration() -> object:
    return SimpleNamespace(
        execution_adapter="openai-agent-runtime/v1",
    )


def _context() -> ReplayExecutionContext:
    return ReplayExecutionContext(
        replay_id="replay-1",
        source_execution_id="agent-runtime-source:core-execution-1",
        new_execution_id="replay-execution-1",
        organization_id="org-a",
        project_id="project-a",
        actor_id="auditor",
        request_id="request-1",
        correlation_id="correlation-1",
        attempt=1,
        cancellation_token=_NotCancelled(),
        metadata={},
        intervention_envelope=ReplayInterventionEnvelope(
            policy_id="policy-1",
            policy_version=1,
            external_execution_id="external-1",
            runtime_tool_call_id="call-openai-1",
            intervention_provider="opaque-reference",
            intervention_provider_version="v1",
            strategy="REPLACE",
            original_evidence_digest="sha256:" + ("a" * 64),
            counterfactual_reference=LOW_RISK_COUNTERFACTUAL.reference,
            counterfactual_digest=LOW_RISK_COUNTERFACTUAL.digest,
            intervention_digest="intervention-digest",
        ),
    )


def _response() -> ReplayHttpResponse:
    return ReplayHttpResponse(
        200,
        {
            "execution_status": "COMPLETED",
            "isolated": True,
            "replay_reference": REPLAY_REFERENCE,
            "source_external_execution_id": "external-1",
            "source_runtime_tool_call_id": "call-openai-1",
            "replay_external_execution_id": "external-replay-1",
            "replay_runtime_tool_call_id": "call-openai-2",
            "counterfactual_evidence_digest": LOW_RISK_COUNTERFACTUAL.digest,
            "outcome_score": 0.0,
            "outcome_ref": f"{REPLAY_REFERENCE}#call-openai-1:intervention-digest",
        },
    )
