from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from openai_agent_runtime.api import create_app
from openai_agent_runtime.contracts import (
    REPLAY_INTERVENTION_ENVELOPE_SCHEMA_VERSION,
)
from openai_agent_runtime.runtime import (
    LOW_RISK_COUNTERFACTUAL,
    OPENAI_FUNCTION_NAME,
    OpenAIAgentRuntime,
)
from openai_agent_runtime.store import InMemoryExecutionStore


class _Responses:
    def __init__(self) -> None:
        self._turn = 0

    def create(self, **_kwargs):
        self._turn += 1
        if self._turn % 2:
            return SimpleNamespace(
                id=f"resp-{self._turn}",
                status="completed",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name=OPENAI_FUNCTION_NAME,
                        call_id=f"provider-call-{self._turn}",
                    )
                ],
            )
        return SimpleNamespace(
            id=f"resp-{self._turn}",
            status="completed",
            output=[],
            output_text=(
                '{"decision":"BLOCK","reason":"high risk"}'
                if self._turn == 2
                else '{"decision":"ALLOW","reason":"low risk"}'
            ),
        )


class _Client:
    def __init__(self) -> None:
        self.responses = _Responses()


def test_replay_requires_bearer_auth_and_accepts_only_the_governed_envelope() -> None:
    runtime = OpenAIAgentRuntime(
        model="test-model", client=_Client(), store=InMemoryExecutionStore()
    )
    client = TestClient(create_app(runtime, "replay-secret"))

    observed = client.post("/executions", json={"scenario": "aligned"}).json()
    assert observed["model_id"] == "test-model"
    assert observed["inference_configuration"] == {
        "function_calling": "required-single-tool",
        "parallel_tool_calls": False,
        "structured_output": "json_schema_strict",
        "response_continuation": "local",
        "response_store": False,
    }
    envelope = {
        "schema_version": REPLAY_INTERVENTION_ENVELOPE_SCHEMA_VERSION,
        "policy_id": "policy-1",
        "policy_version": 1,
        "external_execution_id": observed["external_execution_id"],
        "runtime_tool_call_id": observed["runtime_tool_call_id"],
        "intervention_provider": "opaque-reference",
        "intervention_provider_version": "v1",
        "strategy": "REPLACE",
        "original_evidence_digest": observed["evidence_digest"],
        "counterfactual_reference": LOW_RISK_COUNTERFACTUAL.reference,
        "counterfactual_digest": LOW_RISK_COUNTERFACTUAL.digest,
        "intervention_digest": "intervention-digest",
    }
    payload = {"replay_reference": observed["replay_reference"], "envelope": envelope}

    assert client.post("/replay", json=payload).status_code == 401
    assert (
        client.post(
            "/replay",
            json={
                **payload,
                "envelope": {
                    **envelope,
                    "schema_version": "replay-intervention-envelope/v2",
                },
            },
            headers={"Authorization": "Bearer replay-secret"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/replay",
            json={**payload, "raw_evidence": {"risk_level": "LOW"}},
            headers={"Authorization": "Bearer replay-secret"},
        ).status_code
        == 422
    )

    response = client.post(
        "/replay", json=payload, headers={"Authorization": "Bearer replay-secret"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["counterfactual_evidence_digest"] == LOW_RISK_COUNTERFACTUAL.digest
    assert body["outcome_score"] == 0.0
    assert "decision" not in body
    assert "risk_level" not in str(body)
