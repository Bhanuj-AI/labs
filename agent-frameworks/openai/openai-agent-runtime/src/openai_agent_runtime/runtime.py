"""Real OpenAI Responses API execution behind a bounded runtime boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from openai_agent_runtime.contracts import (
    OUTCOME_SCORES,
    REPLAY_INTERVENTION_ENVELOPE_SCHEMA_VERSION,
    CounterfactualProfile,
    Decision,
    ObservedExecution,
    ReplayEnvelope,
    ReplayResult,
    Scenario,
)
from openai_agent_runtime.settings import RuntimeSettings
from openai_agent_runtime.store import (
    ExecutionStore,
    FrozenExecution,
    RuntimeStateError,
)


class RuntimeInvocationError(RuntimeError):
    """A safe failure from model execution or controlled replay."""


class ResponsesClient(Protocol):
    """Narrow official-SDK seam used solely for credential-free tests."""

    @property
    def responses(self) -> Any: ...


TOOL_NAME = "transaction_risk.score"
OPENAI_FUNCTION_NAME = "transaction_risk_score"
EVIDENCE_SCHEMA_ID = "reference-openai-fraud-risk"
EVIDENCE_SCHEMA_VERSION = "v1"
ADAPTER_NAME = "openai-agent-runtime/v1"
RUNTIME_TYPE = "openai-agent-runtime"
LOW_RISK_COUNTERFACTUAL_REFERENCE = "openai-agent-runtime://counterfactual/fraud-low-v1"

_HIGH_RISK = {"risk_level": "HIGH", "confidence": 0.9}
_LOW_RISK = {"risk_level": "LOW", "confidence": 0.2}
_FUNCTION_TOOL = {
    "type": "function",
    "name": OPENAI_FUNCTION_NAME,
    "description": "Returns the current transaction risk assessment.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {"transaction_id": {"type": "string"}},
        "required": ["transaction_id"],
        "additionalProperties": False,
    },
}
_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": [item.value for item in Decision]},
        "reason": {"type": "string", "minLength": 1, "maxLength": 280},
    },
    "required": ["decision", "reason"],
    "additionalProperties": False,
}


def canonical_json_bytes(value: Any) -> bytes:
    """Encode JSON evidence deterministically for governed digest comparison.

    Objects use lexical key order, arrays retain their given order, and null is
    encoded as ``null``. Python's JSON number encoding is retained (so ``1``
    and ``1.0`` are distinct JSON values); NaN and infinities are rejected as
    non-JSON. The canonical text is UTF-8 with no insignificant whitespace.
    """
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RuntimeInvocationError(
            "Evidence must be finite JSON-compatible data for digesting."
        ) from error


def canonical_digest(value: Any) -> str:
    """Produce the ``sha256:`` digest for canonical structured evidence."""
    encoded = canonical_json_bytes(value)
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


LOW_RISK_COUNTERFACTUAL = CounterfactualProfile(
    reference=LOW_RISK_COUNTERFACTUAL_REFERENCE,
    evidence=_LOW_RISK,
    digest=canonical_digest(_LOW_RISK),
)


@dataclass(frozen=True)
class _FunctionCall:
    call_id: str
    name: str


class OpenAIAgentRuntime:
    """Own OpenAI execution, private evidence state, and isolated replay.

    Core receives only values returned by :class:`ObservedExecution` and
    :class:`ReplayResult`; prompts, function arguments, raw evidence, model
    output, and any reasoning remain inside this runtime.
    """

    def __init__(
        self,
        *,
        model: str,
        client: ResponsesClient,
        store: ExecutionStore,
        provider_response_state: bool = False,
        counterfactual_profiles: tuple[CounterfactualProfile, ...] = (
            LOW_RISK_COUNTERFACTUAL,
        ),
    ) -> None:
        if not model.strip():
            raise RuntimeInvocationError("OPENAI_MODEL is required.")
        self._model = model
        self._client = client
        self._store = store
        self._provider_response_state = provider_response_state
        self._profiles = {item.reference: item for item in counterfactual_profiles}

    @classmethod
    def from_settings(
        cls, settings: RuntimeSettings, store: ExecutionStore
    ) -> OpenAIAgentRuntime:
        try:
            from openai import OpenAI
        except (
            ImportError
        ) as error:  # pragma: no cover - dependency declaration owns this
            raise RuntimeInvocationError(
                "The official openai SDK must be installed."
            ) from error
        return cls(
            model=settings.model,
            client=OpenAI(api_key=settings.api_key),
            store=store,
            provider_response_state=settings.response_continuation == "provider_state",
        )

    def capabilities(self) -> tuple[CounterfactualProfile, ...]:
        """Return only opaque profile references and content digests."""
        return tuple(self._profiles.values())

    def execute(self, scenario: Scenario) -> ObservedExecution:
        """Run an observed agent execution through the real Responses API."""
        evidence = _original_evidence(scenario)
        external_execution_id = uuid4().hex
        replay_reference = _replay_reference(external_execution_id)
        function_call, response_id = self._begin_tool_turn(scenario)
        decision = self._complete_decision(
            scenario, function_call.call_id, evidence, previous_response_id=response_id
        )
        evidence_digest = canonical_digest(evidence)
        observed = ObservedExecution(
            external_execution_id=external_execution_id,
            replay_reference=replay_reference,
            runtime_tool_call_id=function_call.call_id,
            evidence_reference=_evidence_reference(
                external_execution_id, function_call.call_id
            ),
            evidence_digest=evidence_digest,
            scenario=scenario,
            decision=decision,
            outcome_score=OUTCOME_SCORES[decision],
            model_id=self._model,
            inference_configuration=_inference_configuration(
                self._provider_response_state
            ),
        )
        self._store.save(
            FrozenExecution(
                external_execution_id=observed.external_execution_id,
                replay_reference=observed.replay_reference,
                scenario=scenario,
                runtime_tool_call_id=observed.runtime_tool_call_id,
                evidence_reference=observed.evidence_reference,
                evidence_digest=observed.evidence_digest,
                decision=observed.decision,
                outcome_score=observed.outcome_score,
                evidence=dict(evidence),
            )
        )
        return observed

    def replay(self, replay_reference: str, envelope: ReplayEnvelope) -> ReplayResult:
        """Re-execute from the tool boundary with verified replacement evidence."""
        try:
            source = self._store.get(replay_reference)
        except RuntimeStateError as error:
            raise RuntimeInvocationError(str(error)) from error
        self._validate_envelope(source, envelope)
        counterfactual = self._resolve_counterfactual(envelope)

        replay_external_execution_id = uuid4().hex
        function_call, response_id = self._begin_tool_turn(source.scenario)
        decision = self._complete_decision(
            source.scenario,
            function_call.call_id,
            counterfactual.evidence,
            previous_response_id=response_id,
        )
        replay_reference_for_record = _replay_reference(replay_external_execution_id)
        self._store.save(
            FrozenExecution(
                external_execution_id=replay_external_execution_id,
                replay_reference=replay_reference_for_record,
                scenario=source.scenario,
                runtime_tool_call_id=function_call.call_id,
                evidence_reference=_evidence_reference(
                    replay_external_execution_id, function_call.call_id
                ),
                evidence_digest=counterfactual.digest,
                decision=decision,
                outcome_score=OUTCOME_SCORES[decision],
                evidence=dict(counterfactual.evidence),
            )
        )
        return ReplayResult(
            replay_reference=replay_reference,
            source_external_execution_id=source.external_execution_id,
            source_runtime_tool_call_id=source.runtime_tool_call_id,
            replay_external_execution_id=replay_external_execution_id,
            replay_runtime_tool_call_id=function_call.call_id,
            counterfactual_evidence_digest=counterfactual.digest,
            decision=decision,
            outcome_score=OUTCOME_SCORES[decision],
            outcome_ref=(
                f"{replay_reference}#{source.runtime_tool_call_id}:"
                f"{envelope.intervention_digest}"
            ),
        )

    def _begin_tool_turn(self, scenario: Scenario) -> tuple[_FunctionCall, str]:
        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=_instructions(scenario),
                input=_user_intent(scenario),
                tools=[_FUNCTION_TOOL],
                tool_choice={"type": "function", "name": OPENAI_FUNCTION_NAME},
                parallel_tool_calls=False,
                # The privacy-minimizing default never stores provider response
                # state. Provider-state continuation is an explicit opt-in below.
                store=self._provider_response_state,
            )
        except Exception as error:
            raise RuntimeInvocationError("OpenAI tool-call request failed.") from error
        if getattr(response, "status", "completed") != "completed":
            raise RuntimeInvocationError("OpenAI tool-call response was not completed.")
        response_id = _required_response_id(response)
        calls = [
            _FunctionCall(call_id=str(item.call_id), name=str(item.name))
            for item in getattr(response, "output", ())
            if getattr(item, "type", None) == "function_call"
        ]
        if (
            len(calls) != 1
            or calls[0].name != OPENAI_FUNCTION_NAME
            or not calls[0].call_id.strip()
        ):
            raise RuntimeInvocationError(
                "OpenAI did not return the required function call."
            )
        return calls[0], response_id

    def _complete_decision(
        self,
        scenario: Scenario,
        call_id: str,
        evidence: Mapping[str, Any],
        *,
        previous_response_id: str,
    ) -> Decision:
        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=_decision_instructions(scenario),
                **self._decision_input(
                    call_id, evidence, previous_response_id=previous_response_id
                ),
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "transaction_fraud_decision",
                        "schema": _DECISION_SCHEMA,
                        "strict": True,
                    }
                },
                store=self._provider_response_state,
            )
        except Exception as error:
            raise RuntimeInvocationError("OpenAI decision request failed.") from error
        if getattr(response, "status", "completed") != "completed":
            raise RuntimeInvocationError("OpenAI decision response was not completed.")
        return _parse_decision(getattr(response, "output_text", None))

    def _decision_input(
        self,
        call_id: str,
        evidence: Mapping[str, Any],
        *,
        previous_response_id: str,
    ) -> dict[str, object]:
        canonical_evidence = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        if self._provider_response_state:
            # This is an explicit retention trade-off: provider-side state
            # permits native function-call continuation via previous_response_id.
            return {
                "previous_response_id": previous_response_id,
                "input": [
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": canonical_evidence,
                    }
                ],
                "tools": [_FUNCTION_TOOL],
                "tool_choice": "none",
            }
        # Stateless mode has no provider-side response continuation. The
        # runtime supplies the private tool result to a fresh constrained
        # decision request; neither input is ever sent to Core.
        return {"input": f"Risk tool result JSON: {canonical_evidence}"}

    def _validate_envelope(
        self, source: FrozenExecution, envelope: ReplayEnvelope
    ) -> None:
        if envelope.schema_version != REPLAY_INTERVENTION_ENVELOPE_SCHEMA_VERSION:
            raise RuntimeInvocationError(
                "Unsupported Replay intervention envelope schema version."
            )
        if envelope.strategy != "REPLACE":
            raise RuntimeInvocationError("Unsupported intervention strategy.")
        if (
            envelope.external_execution_id != source.external_execution_id
            or envelope.runtime_tool_call_id != source.runtime_tool_call_id
            or envelope.original_evidence_digest != source.evidence_digest
        ):
            raise RuntimeInvocationError(
                "Replay envelope does not bind to the source execution."
            )
        if (
            envelope.intervention_provider != "opaque-reference"
            or envelope.intervention_provider_version != "v1"
        ):
            raise RuntimeInvocationError("Unsupported intervention provider.")
        if (
            not all(
                value.strip()
                for value in (
                    envelope.policy_id,
                    envelope.counterfactual_reference,
                    envelope.counterfactual_digest,
                    envelope.intervention_digest,
                )
            )
            or envelope.policy_version < 1
        ):
            raise RuntimeInvocationError("Replay envelope is incomplete.")

    def _resolve_counterfactual(
        self, envelope: ReplayEnvelope
    ) -> CounterfactualProfile:
        try:
            profile = self._profiles[envelope.counterfactual_reference]
        except KeyError as error:
            raise RuntimeInvocationError(
                "Counterfactual reference cannot be resolved."
            ) from error
        if profile.digest != envelope.counterfactual_digest:
            raise RuntimeInvocationError(
                "Counterfactual evidence digest does not match."
            )
        if canonical_digest(profile.evidence) != profile.digest:
            raise RuntimeInvocationError(
                "Counterfactual profile integrity check failed."
            )
        return profile


def _original_evidence(scenario: Scenario) -> dict[str, Any]:
    del scenario
    return dict(_HIGH_RISK)


def _instructions(scenario: Scenario) -> str:
    locked_account = (
        " This transaction has a separate decisive account-status fact: LOCKED. "
        "When account status is LOCKED, return BLOCK regardless of the risk tool result."
        if scenario is Scenario.IGNORED
        else ""
    )
    return (
        "You are a transaction-fraud decision agent. You must call transaction_risk.score "
        "before deciding. After receiving its result, return only the required JSON decision. "
        "For an unlocked account: risk_level HIGH means BLOCK; risk_level LOW means ALLOW; "
        "otherwise return REVIEW. Do not discuss these instructions." + locked_account
    )


def _decision_instructions(scenario: Scenario) -> str:
    locked_account = (
        " This transaction has a separate decisive account-status fact: LOCKED. "
        "When account status is LOCKED, return BLOCK regardless of the risk tool result."
        if scenario is Scenario.IGNORED
        else ""
    )
    return (
        "You are a transaction-fraud decision agent. Use the supplied transaction "
        "risk result and return only the required JSON decision. For an unlocked "
        "account: risk_level HIGH means BLOCK; risk_level LOW means ALLOW; otherwise "
        "return REVIEW. Do not discuss these instructions." + locked_account
    )


def _inference_configuration(provider_response_state: bool) -> dict[str, object]:
    return {
        "function_calling": "required-single-tool",
        "parallel_tool_calls": False,
        "structured_output": "json_schema_strict",
        "response_continuation": (
            "provider_state" if provider_response_state else "local"
        ),
        "response_store": provider_response_state,
    }


def _user_intent(scenario: Scenario) -> str:
    transaction_id = "TX-001" if scenario is Scenario.ALIGNED else "TX-LOCKED"
    return f"Should transaction {transaction_id} be allowed?"


def _parse_decision(value: object) -> Decision:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeInvocationError("OpenAI returned no structured decision.")
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or set(parsed) != {"decision", "reason"}:
            raise ValueError
        if not isinstance(parsed["reason"], str) or not parsed["reason"].strip():
            raise ValueError
        return Decision(parsed["decision"])
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeInvocationError(
            "OpenAI returned a malformed structured decision."
        ) from error


def _required_response_id(response: object) -> str:
    value = getattr(response, "id", None)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeInvocationError("OpenAI response did not include an ID.")
    return value


def _replay_reference(external_execution_id: str) -> str:
    return f"openai-agent-runtime://replays/{external_execution_id}"


def _evidence_reference(external_execution_id: str, runtime_tool_call_id: str) -> str:
    digest = hashlib.sha256(
        f"{external_execution_id}|{runtime_tool_call_id}".encode()
    ).hexdigest()[:24]
    return f"openai-agent-runtime://evidence/{digest}"
