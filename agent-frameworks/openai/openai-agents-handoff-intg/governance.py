"""Fail-open BHANUJ evidence adapter for public OpenAI Agents SDK hooks.

Handoffs are orchestration ownership transitions, not business function-tool
invocations. The adapter therefore records the transition on paired agent
``WORKFLOW_STEP`` evidence and reserves ``TOOL_CALL`` for ``lookup_policy``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from agents import Agent, RunHooks  # type: ignore

from agent import HANDOFF_TOOL_NAME, POLICY_AGENT_NAME, TRIAGE_AGENT_NAME


AGENT_ID = "openai-agents-claims-handoff"
AGENT_NAME = "OpenAI Agents SDK claims handoff"
AGENT_VERSION = "1.0.0"
RUNTIME_PROVIDER = "openai_agents"
SOURCE_KIND = "openai_agents.agent"


class EvidenceStatus(str, Enum):
    DISABLED = "DISABLED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    DEGRADED = "DEGRADED"


class Transport(Protocol):
    def post(
        self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GovernanceSettings:
    base_url: str
    organization_id: str
    project_id: str

    @classmethod
    def from_environment(cls) -> GovernanceSettings | None:
        values = {
            "AI_GOVERNANCE_BASE_URL": os.getenv("AI_GOVERNANCE_BASE_URL"),
            "AI_GOVERNANCE_ORGANIZATION_ID": os.getenv(
                "AI_GOVERNANCE_ORGANIZATION_ID"
            ),
            "AI_GOVERNANCE_PROJECT_ID": os.getenv("AI_GOVERNANCE_PROJECT_ID"),
        }
        if not any(values.values()):
            return None
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError("AI Governance instrumentation requires " + ", ".join(missing))
        return cls(
            base_url=str(values["AI_GOVERNANCE_BASE_URL"]).rstrip("/"),
            organization_id=str(values["AI_GOVERNANCE_ORGANIZATION_ID"]),
            project_id=str(values["AI_GOVERNANCE_PROJECT_ID"]),
        )


class UrllibTransport:
    """One-shot JSON delivery with no retries and a bounded timeout."""

    def post(
        self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urlopen(request, timeout=1.0) as response:
                body = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Agents Runtime delivery failed: {exc}") from exc
        decoded = json.loads(body) if body else {}
        if not isinstance(decoded, dict):
            raise TypeError("Agents Runtime returned a non-object JSON response.")
        return decoded


class AgentsRuntimeClient:
    """The existing public Agents Runtime execution-evidence contract."""

    def __init__(self, settings: GovernanceSettings, transport: Transport | None = None):
        self._settings = settings
        self._transport = transport or UrllibTransport()

    def start_execution(self, external_execution_id: str) -> str:
        response = self._post(
            "/api/v1/agent-executions",
            {
                "agent_id": AGENT_ID,
                "agent_name": AGENT_NAME,
                "agent_version": AGENT_VERSION,
                "external_execution_id": external_execution_id,
                "runtime_provider": RUNTIME_PROVIDER,
                "correlation_id": external_execution_id,
                "metadata": {"workflow": "claims_handoff"},
            },
        )
        try:
            return str(response["execution"]["execution_id"])
        except (KeyError, TypeError) as exc:
            raise RuntimeError("Agents Runtime returned no execution ID.") from exc

    def append_event(self, execution_id: str, payload: Mapping[str, Any]) -> None:
        self._post(f"/api/v1/agent-executions/{execution_id}/events", payload)

    def complete_execution(self, execution_id: str, status: str) -> None:
        self._post(
            f"/api/v1/agent-executions/{execution_id}/complete", {"status": status}
        )

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._transport.post(
            f"{self._settings.base_url}{path}",
            {
                "X-AI-Governance-Organization-Id": self._settings.organization_id,
                "X-AI-Governance-Project-Id": self._settings.project_id,
            },
            payload,
        )


@dataclass
class AgentActivation:
    agent_name: str
    ordinal: int
    step_id: str
    completed: bool = False


class ExecutionEvidenceReporter:
    """Observes one SDK run without altering SDK ownership or error semantics."""

    def __init__(
        self, client: AgentsRuntimeClient | None, *, external_execution_id: str | None = None
    ) -> None:
        self._client = client
        self.external_execution_id = external_execution_id or f"openai-agents-{uuid4()}"
        self.execution_id: str | None = None
        self.status = EvidenceStatus.DISABLED if client is None else EvidenceStatus.RUNNING
        self.error: str | None = None
        self._activations: dict[int, AgentActivation] = {}
        self._activation_counts: dict[str, int] = {}
        self._pending_handoff_from: dict[int, str] = {}
        self._model_started_at: list[float] = []
        self._model_ordinal = 0
        self._tool_ordinal = 0
        self._tool_started_at: dict[str, float] = {}

    @classmethod
    def from_environment(cls) -> ExecutionEvidenceReporter:
        try:
            settings = GovernanceSettings.from_environment()
        except ValueError as exc:
            reporter = cls(None)
            reporter.status, reporter.error = EvidenceStatus.DEGRADED, str(exc)
            return reporter
        return cls(AgentsRuntimeClient(settings) if settings else None)

    def begin(self) -> None:
        if self._client is None:
            return
        try:
            self.execution_id = self._client.start_execution(self.external_execution_id)
        except Exception as exc:  # noqa: BLE001 - independent evidence plane
            self._degrade(exc)

    def complete(self, status: str) -> None:
        if not self._can_deliver():
            return
        try:
            self._client.complete_execution(str(self.execution_id), status)
            if status == "SUCCEEDED":
                self.status = EvidenceStatus.SUCCEEDED
        except Exception as exc:  # noqa: BLE001 - independent evidence plane
            self._degrade(exc)

    def agent_started(self, agent: Agent[Any]) -> None:
        activation = self._new_activation(agent.name)
        self._activations[id(agent)] = activation
        attributes: dict[str, object] = {"status": "STARTED"}
        handoff_from = self._pending_handoff_from.pop(id(agent), None)
        if handoff_from:
            attributes.update(
                {
                    "transition_type": "HANDOFF",
                    "handoff_from_agent": handoff_from,
                    "handoff_to_agent": agent.name,
                }
            )
        self._emit_step(activation, "STARTED", attributes)

    def handoff(self, from_agent: Agent[Any], to_agent: Agent[Any]) -> None:
        from_activation = self._activations.get(id(from_agent))
        if from_activation is not None and not from_activation.completed:
            self._emit_step(
                from_activation,
                "COMPLETED",
                {
                    "status": "COMPLETED",
                    "transition_type": "HANDOFF",
                    "handoff_from_agent": from_agent.name,
                    "handoff_to_agent": to_agent.name,
                },
            )
            from_activation.completed = True
        self._pending_handoff_from[id(to_agent)] = from_agent.name

    def agent_completed(self, agent: Agent[Any]) -> None:
        activation = self._activations.get(id(agent))
        if activation is None or activation.completed:
            return
        self._emit_step(activation, "COMPLETED", {"status": "COMPLETED"})
        activation.completed = True

    def fail_active_agent(self) -> None:
        active = [activation for activation in self._activations.values() if not activation.completed]
        if not active:
            return
        activation = max(active, key=lambda item: item.ordinal)
        self._emit_step(activation, "FAILED", {"status": "FAILED"})
        activation.completed = True

    def model_started(self) -> None:
        self._model_started_at.append(time.monotonic())

    def model_completed(self, agent: Agent[Any], response: Any) -> None:
        started_at = self._model_started_at.pop(0) if self._model_started_at else time.monotonic()
        self._model_ordinal += 1
        self._append(
            {
                "event_type": "MODEL_CALL",
                "actor_id": _model_identifier(agent),
                "actor_type": "MODEL",
                "attributes": {
                    "model": _model_identifier(agent),
                    "agent_name": agent.name,
                    "ordinal": self._model_ordinal,
                    "status": "COMPLETED",
                    "latency_ms": _elapsed_ms(started_at),
                    **_usage_counts(getattr(response, "usage", None)),
                },
                "idempotency_key": f"{self.external_execution_id}:model:{self._model_ordinal}",
            }
        )

    def tool_started(self, context: Any, tool: Any) -> None:
        if getattr(tool, "name", None) == HANDOFF_TOOL_NAME:
            return
        self._tool_ordinal += 1
        self._tool_started_at[_tool_key(context, self._tool_ordinal)] = time.monotonic()

    def tool_completed(self, context: Any, tool: Any) -> None:
        if getattr(tool, "name", None) == HANDOFF_TOOL_NAME:
            return
        key = _tool_key(context, self._tool_ordinal)
        started_at = self._tool_started_at.pop(key, time.monotonic())
        call_id = _public_text(getattr(context, "tool_call_id", None))
        attributes: dict[str, object] = {
            "status": "COMPLETED",
            "latency_ms": _elapsed_ms(started_at),
        }
        if call_id:
            attributes["tool_call_id"] = call_id
        self._append(
            {
                "event_type": "TOOL_CALL",
                "actor_id": str(getattr(tool, "name", "lookup_policy")),
                "actor_type": "TOOL",
                "attributes": attributes,
                "idempotency_key": f"{self.external_execution_id}:tool:{call_id or self._tool_ordinal}",
            }
        )

    def _new_activation(self, agent_name: str) -> AgentActivation:
        ordinal = self._activation_counts.get(agent_name, 0) + 1
        self._activation_counts[agent_name] = ordinal
        digest = hashlib.sha256(
            f"{self.external_execution_id}:{agent_name}:{ordinal}".encode()
        ).hexdigest()[:24]
        return AgentActivation(agent_name, ordinal, f"step-{digest}")

    def _emit_step(
        self, activation: AgentActivation, lifecycle: str, attributes: Mapping[str, object]
    ) -> None:
        self._append(
            {
                "event_type": "WORKFLOW_STEP",
                "step_id": activation.step_id,
                "step_name": activation.agent_name,
                "lifecycle": lifecycle,
                "source_kind": SOURCE_KIND,
                "actor_id": activation.agent_name,
                "actor_type": "AGENT",
                "attributes": dict(attributes),
                "idempotency_key": f"{activation.step_id}:{lifecycle}",
            }
        )

    def _append(self, payload: Mapping[str, Any]) -> None:
        if not self._can_deliver():
            return
        try:
            self._client.append_event(str(self.execution_id), payload)
        except Exception as exc:  # noqa: BLE001 - independent evidence plane
            self._degrade(exc)

    def _can_deliver(self) -> bool:
        return self._client is not None and self.execution_id is not None and self.status is EvidenceStatus.RUNNING

    def _degrade(self, error: Exception) -> None:
        self.status = EvidenceStatus.DEGRADED
        self.error = str(error)


class GovernedRunHooks(RunHooks):
    """Public lifecycle callbacks; no prompts, outputs, arguments, or raw objects persist."""

    def __init__(self, reporter: ExecutionEvidenceReporter):
        self._reporter = reporter

    async def on_agent_start(self, context: Any, agent: Agent[Any]) -> None:
        del context
        self._reporter.agent_started(agent)

    async def on_agent_end(self, context: Any, agent: Agent[Any], output: Any) -> None:
        del context, output
        self._reporter.agent_completed(agent)

    async def on_handoff(
        self, context: Any, from_agent: Agent[Any], to_agent: Agent[Any]
    ) -> None:
        del context
        self._reporter.handoff(from_agent, to_agent)

    async def on_llm_start(
        self, context: Any, agent: Agent[Any], system_prompt: str | None, input_items: list[Any]
    ) -> None:
        del context, agent, system_prompt, input_items
        self._reporter.model_started()

    async def on_llm_end(self, context: Any, agent: Agent[Any], response: Any) -> None:
        del context
        self._reporter.model_completed(agent, response)

    async def on_tool_start(self, context: Any, agent: Agent[Any], tool: Any) -> None:
        del agent
        self._reporter.tool_started(context, tool)

    async def on_tool_end(
        self, context: Any, agent: Agent[Any], tool: Any, result: object
    ) -> None:
        del agent, result
        self._reporter.tool_completed(context, tool)


def _model_identifier(agent: Agent[Any]) -> str:
    model = getattr(agent, "model", None)
    return model if isinstance(model, str) and model else "configured-model"


def _usage_counts(usage: Any) -> dict[str, int]:
    values: dict[str, int] = {}
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        value = getattr(usage, name, None)
        if isinstance(value, int) and value >= 0:
            values[name] = value
    return values


def _tool_key(context: Any, fallback: int) -> str:
    return _public_text(getattr(context, "tool_call_id", None)) or f"tool-{fallback}"


def _public_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))
