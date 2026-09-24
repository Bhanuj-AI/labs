"""Core plugin adapter that calls the external runtime without importing OpenAI."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from bhanuj_governance_plugin_api import (
    ReplayExecutionContext,
    ReplayExecutionResult,
)


class OpenAIAgentRuntimeReplayError(RuntimeError):
    """The external runtime did not safely complete a governed Replay."""


@dataclass(frozen=True)
class ReplayHttpResponse:
    status_code: int
    payload: Mapping[str, Any]


class ReplayTransport(Protocol):
    def post(
        self,
        endpoint: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> ReplayHttpResponse: ...


class UrlLibReplayTransport:
    def post(
        self,
        endpoint: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> ReplayHttpResponse:
        request = Request(
            endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                value = json.load(response)
                status_code = response.getcode()
        except HTTPError as error:
            return ReplayHttpResponse(error.code, {})
        except (URLError, OSError, TimeoutError) as error:
            raise OpenAIAgentRuntimeReplayError(
                "OpenAI agent runtime endpoint is unavailable."
            ) from error
        if not isinstance(value, Mapping):
            raise OpenAIAgentRuntimeReplayError(
                "OpenAI agent runtime returned malformed replay JSON."
            )
        return ReplayHttpResponse(int(status_code), value)


class OpenAIAgentRuntimeReplayAdapter:
    """Provider-neutral Core adapter for the separately owned runtime endpoint."""

    name = "openai-agent-runtime/v1"
    _REFERENCE = re.compile(r"^openai-agent-runtime://replays/[0-9a-f-]{1,255}$")
    _DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

    def __init__(
        self,
        *,
        approved_endpoint: str | None = None,
        transport: ReplayTransport | None = None,
        token_provider: Callable[[], str | None] | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        self._approved_endpoint = _normalise_endpoint(
            approved_endpoint
            if approved_endpoint is not None
            else os.getenv("AI_GOVERNANCE_OPENAI_AGENT_RUNTIME_REPLAY_ENDPOINT")
        )
        self._transport = transport or UrlLibReplayTransport()
        self._token_provider = token_provider or _replay_bearer_from_environment
        self._timeout_seconds = timeout_seconds

    def validate_configuration(
        self, source_execution: object, configuration: object
    ) -> None:
        capability = _capability(source_execution)
        if capability.get("adapter_id") != self.name:
            raise OpenAIAgentRuntimeReplayError(
                "Replay source does not declare the OpenAI agent runtime adapter."
            )
        if capability.get("runtime_type") != "openai-agent-runtime":
            raise OpenAIAgentRuntimeReplayError(
                "Replay source runtime type is unsupported."
            )
        if capability.get("adapter_version") != "v1":
            raise OpenAIAgentRuntimeReplayError(
                "Replay source adapter version is unsupported."
            )
        if getattr(configuration, "execution_adapter", None) != self.name:
            raise OpenAIAgentRuntimeReplayError(
                "Frozen replay configuration selected another adapter."
            )
        reference = capability.get("replay_reference")
        if not isinstance(reference, str) or not self._REFERENCE.fullmatch(reference):
            raise OpenAIAgentRuntimeReplayError("Replay source reference is invalid.")
        endpoint = _normalise_endpoint(capability.get("endpoint"))
        if self._approved_endpoint is None or endpoint != self._approved_endpoint:
            raise OpenAIAgentRuntimeReplayError(
                "Replay endpoint is not approved for this worker."
            )
        if "REPLACE" not in _supported_interventions(capability):
            raise OpenAIAgentRuntimeReplayError(
                "Replay source does not support REPLACE."
            )

    def replay(
        self,
        source_execution: object,
        configuration: object,
        context: ReplayExecutionContext,
    ) -> ReplayExecutionResult:
        self.validate_configuration(source_execution, configuration)
        if context.cancellation_token.is_cancelled:
            raise OpenAIAgentRuntimeReplayError("Replay cancellation requested.")
        envelope = context.intervention_envelope
        if envelope is None:
            raise OpenAIAgentRuntimeReplayError(
                "OpenAI controlled replay requires a governed intervention envelope."
            )
        if envelope.strategy != "REPLACE":
            raise OpenAIAgentRuntimeReplayError("OpenAI runtime supports only REPLACE.")
        response = self._call_runtime(
            {
                "replay_reference": _capability(source_execution)["replay_reference"],
                "envelope": envelope.to_payload(),
            },
            context.replay_id,
        )
        return _workflow_execution(source_execution, context, envelope, response)

    def _call_runtime(
        self, payload: Mapping[str, Any], replay_id: str
    ) -> ReplayHttpResponse:
        headers: dict[str, str] = {"Idempotency-Key": replay_id}
        token = self._token_provider()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self._transport.post(
            self._approved_endpoint or "", payload, headers, self._timeout_seconds
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise OpenAIAgentRuntimeReplayError(
                f"OpenAI agent runtime rejected replay (HTTP {response.status_code})."
            )
        return response


def _workflow_execution(
    source: object,
    context: ReplayExecutionContext,
    envelope,
    response: ReplayHttpResponse,
) -> ReplayExecutionResult:
    payload = response.payload
    score = payload.get("outcome_score")
    counterfactual_digest = payload.get("counterfactual_evidence_digest")
    if (
        payload.get("execution_status") != "COMPLETED"
        or payload.get("isolated") is not True
        or payload.get("replay_reference")
        != _capability(source).get("replay_reference")
        or payload.get("source_external_execution_id") != envelope.external_execution_id
        or payload.get("source_runtime_tool_call_id") != envelope.runtime_tool_call_id
        or counterfactual_digest != envelope.counterfactual_digest
        or not isinstance(score, (int, float))
        or isinstance(score, bool)
        or not math.isfinite(float(score))
        or not 0.0 <= float(score) <= 1.0
        or not isinstance(payload.get("outcome_ref"), str)
        or not payload["outcome_ref"].strip()
        or not isinstance(payload.get("replay_external_execution_id"), str)
        or not payload["replay_external_execution_id"].strip()
    ):
        raise OpenAIAgentRuntimeReplayError(
            "OpenAI agent runtime returned an unsafe or malformed replay response."
        )
    return ReplayExecutionResult(
        execution_status="COMPLETED",
        input={},
        final_state={
            "causal_audit_outcome_score": float(score),
            "outcome_ref": payload["outcome_ref"],
            "counterfactual_evidence_digest": counterfactual_digest,
        },
        events=[
            {
                "type": "EXTERNAL_RUNTIME_REPLAY_COMPLETED",
                "adapter_id": OpenAIAgentRuntimeReplayAdapter.name,
                "intervention_digest": envelope.intervention_digest,
            }
        ],
        artifact_refs=[
            envelope.counterfactual_reference,
            *(_first_artifact_ref(source),),
        ],
        runtime_parameters={"isolated": True},
        metadata={
            "external_runtime": {
                "replay_reference": _capability(source).get("replay_reference"),
                "replay_external_execution_id": payload["replay_external_execution_id"],
                "intervention_digest": envelope.intervention_digest,
                "counterfactual_evidence_digest": counterfactual_digest,
            }
        },
        created_at=datetime.now(UTC),
    )


def _capability(source_execution: object) -> Mapping[str, Any]:
    runtime_parameters = getattr(source_execution, "runtime_parameters", None)
    value = (
        runtime_parameters.get("agent_runtime_replay")
        if isinstance(runtime_parameters, Mapping)
        else None
    )
    if not isinstance(value, Mapping):
        raise OpenAIAgentRuntimeReplayError("Replay source has no runtime capability.")
    return value


def _supported_interventions(capability: Mapping[str, Any]) -> set[str]:
    values = capability.get("supported_interventions")
    if not isinstance(values, list):
        return set()
    try:
        return {value for value in values if isinstance(value, str)}
    except TypeError:
        return set()


def _first_artifact_ref(source_execution: object) -> tuple[str, ...]:
    artifact_refs = getattr(source_execution, "artifact_refs", None)
    if not isinstance(artifact_refs, (list, tuple)) or not artifact_refs:
        return ()
    first = artifact_refs[0]
    return (first,) if isinstance(first, str) and first.strip() else ()


def _normalise_endpoint(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlparse(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/replay"
    ):
        return None
    return value.strip().rstrip("/")


def _replay_bearer_from_environment() -> str | None:
    return (
        os.getenv("AI_GOVERNANCE_OPENAI_AGENT_RUNTIME_REPLAY_AUTH_TOKEN", "").strip()
        or None
    )
