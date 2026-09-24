from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from openai_agent_runtime.contracts import ReplayEnvelope, Scenario
from openai_agent_runtime.runtime import (
    LOW_RISK_COUNTERFACTUAL,
    OPENAI_FUNCTION_NAME,
    OpenAIAgentRuntime,
    RuntimeInvocationError,
    canonical_digest,
    canonical_json_bytes,
)
from openai_agent_runtime.settings import (
    RuntimeConfigurationError,
    RuntimeSettings,
)
from openai_agent_runtime.store import InMemoryExecutionStore


class _Responses:
    def __init__(self, decisions: list[str]) -> None:
        self.calls: list[dict[str, object]] = []
        self._decisions = iter(decisions)
        self._turn = 0

    def create(self, **kwargs):
        self.calls.append(kwargs)
        self._turn += 1
        if self._turn % 2:
            return SimpleNamespace(
                id=f"resp-{self._turn}",
                status="completed",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name=OPENAI_FUNCTION_NAME,
                        call_id=f"call-openai-{self._turn}",
                    )
                ],
            )
        return SimpleNamespace(
            id=f"resp-{self._turn}",
            status="completed",
            output=[],
            output_text=next(self._decisions),
        )


class _Client:
    def __init__(self, decisions: list[str]) -> None:
        self.responses = _Responses(decisions)


def _runtime(*decisions: str) -> tuple[OpenAIAgentRuntime, _Client]:
    client = _Client(list(decisions))
    return OpenAIAgentRuntime(
        model="test-model", client=client, store=InMemoryExecutionStore()
    ), client


def _envelope(
    observed, *, digest: str | None = None, schema_version: str | None = None
) -> ReplayEnvelope:
    return ReplayEnvelope(
        policy_id="fraud-low-risk-policy",
        policy_version=1,
        external_execution_id=observed.external_execution_id,
        runtime_tool_call_id=observed.runtime_tool_call_id,
        intervention_provider="opaque-reference",
        intervention_provider_version="v1",
        strategy="REPLACE",
        original_evidence_digest=observed.evidence_digest,
        counterfactual_reference=LOW_RISK_COUNTERFACTUAL.reference,
        counterfactual_digest=digest or LOW_RISK_COUNTERFACTUAL.digest,
        intervention_digest="intervention-digest",
        schema_version=schema_version or "replay-intervention-envelope/v1",
    )


def test_aligned_execution_uses_the_provider_function_call_id_and_replays_low_risk() -> (
    None
):
    runtime, client = _runtime(
        '{"decision":"BLOCK","reason":"high risk"}',
        '{"decision":"ALLOW","reason":"low risk"}',
    )

    observed = runtime.execute(Scenario.ALIGNED)
    replayed = runtime.replay(observed.replay_reference, _envelope(observed))

    assert observed.runtime_tool_call_id == "call-openai-1"
    assert observed.decision == "BLOCK"
    assert replayed.replay_runtime_tool_call_id == "call-openai-3"
    assert replayed.decision == "ALLOW"
    assert replayed.outcome_score == 0.0
    assert replayed.counterfactual_evidence_digest == LOW_RISK_COUNTERFACTUAL.digest
    assert client.responses.calls[0]["tools"][0]["name"] == OPENAI_FUNCTION_NAME
    assert client.responses.calls[0]["tool_choice"] == {
        "type": "function",
        "name": OPENAI_FUNCTION_NAME,
    }
    assert "previous_response_id" not in client.responses.calls[1]
    assert client.responses.calls[1]["input"] == (
        'Risk tool result JSON: {"confidence":0.9,"risk_level":"HIGH"}'
    )
    assert client.responses.calls[0]["store"] is False
    assert client.responses.calls[1]["store"] is False


def test_provider_state_continuation_is_explicit_and_uses_response_storage() -> None:
    client = _Client(['{"decision":"BLOCK","reason":"high risk"}'])
    runtime = OpenAIAgentRuntime(
        model="test-model",
        client=client,
        store=InMemoryExecutionStore(),
        provider_response_state=True,
    )

    observed = runtime.execute(Scenario.ALIGNED)

    assert observed.inference_configuration["response_continuation"] == "provider_state"
    assert observed.inference_configuration["response_store"] is True
    assert client.responses.calls[1]["previous_response_id"] == "resp-1"
    assert client.responses.calls[1]["input"][0]["call_id"] == "call-openai-1"
    assert client.responses.calls[0]["store"] is True
    assert client.responses.calls[1]["store"] is True


def test_ignored_fixture_still_calls_and_replaces_tool_evidence_but_outcome_is_unchanged() -> (
    None
):
    runtime, _client = _runtime(
        '{"decision":"BLOCK","reason":"account locked"}',
        '{"decision":"BLOCK","reason":"account locked"}',
    )

    observed = runtime.execute(Scenario.IGNORED)
    replayed = runtime.replay(observed.replay_reference, _envelope(observed))

    assert observed.runtime_tool_call_id
    assert observed.outcome_score == replayed.outcome_score == 1.0
    assert replayed.counterfactual_evidence_digest != observed.evidence_digest


def test_unknown_reference_and_digest_mismatch_fail_before_a_replay_model_call() -> (
    None
):
    runtime, client = _runtime('{"decision":"BLOCK","reason":"high risk"}')
    observed = runtime.execute(Scenario.ALIGNED)
    unknown = _envelope(observed)
    unknown = ReplayEnvelope(
        **{**vars(unknown), "counterfactual_reference": "runtime://unknown"}
    )

    with pytest.raises(RuntimeInvocationError, match="cannot be resolved"):
        runtime.replay(observed.replay_reference, unknown)
    with pytest.raises(RuntimeInvocationError, match="digest does not match"):
        runtime.replay(
            observed.replay_reference, _envelope(observed, digest="sha256:wrong")
        )

    assert len(client.responses.calls) == 2


def test_unknown_runtime_tool_call_and_unsupported_envelope_version_fail_closed() -> (
    None
):
    runtime, client = _runtime('{"decision":"BLOCK","reason":"high risk"}')
    observed = runtime.execute(Scenario.ALIGNED)

    with pytest.raises(RuntimeInvocationError, match="does not bind"):
        runtime.replay(
            observed.replay_reference,
            ReplayEnvelope(
                **{**vars(_envelope(observed)), "runtime_tool_call_id": "unknown"}
            ),
        )
    with pytest.raises(RuntimeInvocationError, match="Unsupported Replay intervention"):
        runtime.replay(
            observed.replay_reference,
            _envelope(observed, schema_version="replay-intervention-envelope/v2"),
        )

    assert len(client.responses.calls) == 2


def test_canonical_evidence_digest_ignores_json_whitespace_and_object_key_order() -> (
    None
):
    compact = '{"risk_level":"LOW","confidence":0.2}'
    formatted = """
    {
      "confidence": 0.2,
      "risk_level": "LOW"
    }
    """

    assert canonical_digest(json.loads(compact)) == canonical_digest(
        json.loads(formatted)
    )
    assert (
        canonical_json_bytes({"values": [None, 2, 1.0]}) == b'{"values":[null,2,1.0]}'
    )
    assert canonical_digest({"value": 1}) != canonical_digest({"value": 1.0})
    with pytest.raises(RuntimeInvocationError, match="finite JSON-compatible"):
        canonical_digest({"value": float("nan")})


def test_malformed_model_decision_is_rejected_without_a_fabricated_outcome() -> None:
    runtime, _client = _runtime("not json")

    with pytest.raises(RuntimeInvocationError, match="malformed structured decision"):
        runtime.execute(Scenario.ALIGNED)


class _FailingResponses:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        raise self._error


class _FailingClient:
    def __init__(self, error: Exception) -> None:
        self.responses = _FailingResponses(error)


@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError("api unavailable")])
def test_openai_timeout_or_api_failure_never_fabricates_an_outcome(
    error: Exception,
) -> None:
    runtime = OpenAIAgentRuntime(
        model="test-model",
        client=_FailingClient(error),
        store=InMemoryExecutionStore(),
    )

    with pytest.raises(RuntimeInvocationError, match="tool-call request failed"):
        runtime.execute(Scenario.ALIGNED)


def test_partial_replay_failure_preserves_the_original_frozen_execution() -> None:
    store = InMemoryExecutionStore()
    runtime, _client = _runtime('{"decision":"BLOCK","reason":"high risk"}')
    runtime._store = store
    observed = runtime.execute(Scenario.ALIGNED)
    original = store.get(observed.replay_reference)
    runtime._client = _FailingClient(TimeoutError())

    with pytest.raises(RuntimeInvocationError, match="tool-call request failed"):
        runtime.replay(observed.replay_reference, _envelope(observed))

    assert store.get(observed.replay_reference) == original


def test_missing_openai_key_fails_clearly(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_AGENT_RUNTIME_REPLAY_AUTH_TOKEN", "test-token")

    with pytest.raises(RuntimeConfigurationError, match="OPENAI_API_KEY is required"):
        RuntimeSettings.from_environment()


def test_response_continuation_mode_defaults_to_local_and_rejects_unknown_values(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_AGENT_RUNTIME_REPLAY_AUTH_TOKEN", "test-token")
    monkeypatch.delenv("OPENAI_AGENT_RUNTIME_RESPONSE_CONTINUATION", raising=False)

    assert RuntimeSettings.from_environment().response_continuation == "local"

    monkeypatch.setenv("OPENAI_AGENT_RUNTIME_RESPONSE_CONTINUATION", "unbounded")
    with pytest.raises(RuntimeConfigurationError, match="must be 'local' or"):
        RuntimeSettings.from_environment()
