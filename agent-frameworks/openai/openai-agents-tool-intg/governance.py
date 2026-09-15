"""Fail-open BHANUJ execution-evidence adapter for the public Agents SDK hooks.

The agent and function tool do not import this module.  It observes only
bounded lifecycle facts through the public ``RunHooks`` API, then sends them
one-way to the public Agents Runtime REST contract.
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

from agents import Agent, RunHooks # type: ignore

AGENT_ID = "openai-agents-policy-validation-agent"
AGENT_NAME = "Policy Validation Agent"
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
    """One-shot JSON delivery with a small bounded timeout and no retries."""

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
                "metadata": {"workflow": "policy_validation"},
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


class ExecutionEvidenceReporter:
    """A one-run observer. Delivery errors never influence SDK execution."""

    def __init__(
        self, client: AgentsRuntimeClient | None, *, external_execution_id: str | None = None
    ) -> None:
        self._client = client
        self.external_execution_id = external_execution_id or f"openai-agents-{uuid4()}"
        self.execution_id: str | None = None
        self.status = EvidenceStatus.DISABLED if client is None else EvidenceStatus.RUNNING
        self.error: str | None = None
        self._model_started_at: list[float] = []
        self._tool_started_at: dict[str, float] = {}
        self._model_ordinal = 0
        self._tool_ordinal = 0

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
        self._append(
            {
                "event_type": "WORKFLOW_STEP",
                "step_id": self._step_id(agent.name),
                "step_name": agent.name,
                "lifecycle": "STARTED",
                "source_kind": SOURCE_KIND,
                "actor_id": AGENT_ID,
                "actor_type": "AGENT",
                "attributes": {"status": "STARTED"},
                "idempotency_key": f"{self._step_id(agent.name)}:STARTED",
            }
        )

    def agent_completed(self, agent: Agent[Any]) -> None:
        self._append(
            {
                "event_type": "WORKFLOW_STEP",
                "step_id": self._step_id(agent.name),
                "step_name": agent.name,
                "lifecycle": "COMPLETED",
                "source_kind": SOURCE_KIND,
                "actor_id": AGENT_ID,
                "actor_type": "AGENT",
                "attributes": {"status": "COMPLETED"},
                "idempotency_key": f"{self._step_id(agent.name)}:COMPLETED",
            }
        )

    def agent_failed(self, agent: Agent[Any]) -> None:
        self._append(
            {
                "event_type": "WORKFLOW_STEP",
                "step_id": self._step_id(agent.name),
                "step_name": agent.name,
                "lifecycle": "FAILED",
                "source_kind": SOURCE_KIND,
                "actor_id": AGENT_ID,
                "actor_type": "AGENT",
                "attributes": {"status": "FAILED"},
                "idempotency_key": f"{self._step_id(agent.name)}:FAILED",
            }
        )

    def model_started(self) -> None:
        self._model_started_at.append(time.monotonic())

    def model_completed(self, agent: Agent[Any], response: Any) -> None:
        started_at = self._model_started_at.pop(0) if self._model_started_at else time.monotonic()
        self._model_ordinal += 1
        usage = getattr(response, "usage", None)
        attributes = {
            "model": _model_identifier(agent),
            "ordinal": self._model_ordinal,
            "status": "COMPLETED",
            "latency_ms": _elapsed_ms(started_at),
            **_usage_counts(usage),
        }
        self._append(
            {
                "event_type": "MODEL_CALL",
                "actor_id": _model_identifier(agent),
                "actor_type": "MODEL",
                "attributes": attributes,
                "idempotency_key": f"{self.external_execution_id}:model:{self._model_ordinal}",
            }
        )

    def tool_started(self, context: Any) -> None:
        self._tool_ordinal += 1
        self._tool_started_at[_tool_key(context, self._tool_ordinal)] = time.monotonic()

    def tool_completed(self, context: Any, tool: Any) -> None:
        key = _tool_key(context, self._tool_ordinal)
        started_at = self._tool_started_at.pop(key, time.monotonic())
        call_id = _public_text(getattr(context, "tool_call_id", None))
        attributes: dict[str, Any] = {
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

    def _append(self, payload: Mapping[str, Any]) -> None:
        if not self._can_deliver():
            return
        try:
            self._client.append_event(str(self.execution_id), payload)
        except Exception as exc:  # noqa: BLE001 - independent evidence plane
            self._degrade(exc)

    def _can_deliver(self) -> bool:
        return self._client is not None and self.execution_id is not None and self.status is EvidenceStatus.RUNNING

    def _step_id(self, agent_name: str) -> str:
        digest = hashlib.sha256(f"{self.external_execution_id}:{agent_name}".encode()).hexdigest()[:24]
        return f"step-{digest}"

    def _degrade(self, error: Exception) -> None:
        self.status = EvidenceStatus.DEGRADED
        self.error = str(error)


class GovernedRunHooks(RunHooks):
    """Public SDK lifecycle hook adapter; it never inspects prompts or outputs."""

    def __init__(self, reporter: ExecutionEvidenceReporter):
        self._reporter = reporter

    async def on_agent_start(self, context: Any, agent: Agent[Any]) -> None:
        del context
        self._reporter.agent_started(agent)

    async def on_agent_end(self, context: Any, agent: Agent[Any], output: Any) -> None:
        del context, output
        self._reporter.agent_completed(agent)

    async def on_llm_start(self, context: Any, agent: Agent[Any], system_prompt: str | None, input_items: list[Any]) -> None:
        del context, agent, system_prompt, input_items
        self._reporter.model_started()

    async def on_llm_end(self, context: Any, agent: Agent[Any], response: Any) -> None:
        del context
        self._reporter.model_completed(agent, response)

    async def on_tool_start(self, context: Any, agent: Agent[Any], tool: Any) -> None:
        del agent, tool
        self._reporter.tool_started(context)

    async def on_tool_end(self, context: Any, agent: Agent[Any], tool: Any, result: object) -> None:
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
