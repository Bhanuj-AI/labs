"""Fail-open BHANUJ Platform runtime-evidence adapter.

This module deliberately contains no policy business logic and never sends
Claude messages, tool arguments, tool results, or response text as evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import AssistantMessage


RUNTIME_PROVIDER = "claude_agent_sdk"
AGENT_ID = "claude-policy-validation-agent"
AGENT_NAME = "Policy Validation Agent"
AGENT_VERSION = "1.0.0"
SOURCE_KIND = "claude_agent_sdk.session"
POLICY_TOOL_NAME = "mcp__policy__lookup_policy"


@dataclass(frozen=True)
class GovernanceSettings:
    base_url: str
    organization_id: str
    project_id: str

    @classmethod
    def from_environment(cls) -> "GovernanceSettings | None":
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
            raise ValueError(
                "BHANUJ Platform instrumentation requires " + ", ".join(missing)
            )
        return cls(
            base_url=str(values["AI_GOVERNANCE_BASE_URL"]).rstrip("/"),
            organization_id=str(values["AI_GOVERNANCE_ORGANIZATION_ID"]),
            project_id=str(values["AI_GOVERNANCE_PROJECT_ID"]),
        )


class AgentsRuntimeClient:
    """Small public REST adapter with bounded, fail-open delivery."""

    def __init__(self, settings: GovernanceSettings) -> None:
        self._settings = settings

    def _request(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        headers = {"Content-Type": "application/json"}
        headers["X-AI-Governance-Organization-Id"] = self._settings.organization_id
        headers["X-AI-Governance-Project-Id"] = self._settings.project_id
        request = urllib.request.Request(
            f"{self._settings.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload) if payload else {}
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            OSError,
            ValueError,
        ):
            return None

    def create_execution(self, external_execution_id: str) -> str | None:
        response = self._request(
            "POST",
            "/api/v1/agent-executions",
            {
                "external_execution_id": external_execution_id,
                "runtime_provider": RUNTIME_PROVIDER,
                "agent_id": AGENT_ID,
                "agent_name": AGENT_NAME,
                "agent_version": AGENT_VERSION,
                "correlation_id": external_execution_id,
                "metadata": {"workflow": "policy_validation"},
            },
        )
        if not response:
            return None
        execution = response.get("execution")
        execution_id = execution.get("execution_id") if isinstance(execution, dict) else None
        return str(execution_id) if execution_id else None

    def append_event(self, execution_id: str, event: dict[str, Any]) -> bool:
        return self._request(
            "POST",
            f"/api/v1/agent-executions/{execution_id}/events",
            event,
        ) is not None

    def complete_execution(self, execution_id: str, status: str) -> bool:
        return self._request(
            "POST",
            f"/api/v1/agent-executions/{execution_id}/complete",
            {"status": status},
        ) is not None


class EvidenceReporter:
    """Records operational facts only; all delivery failures are isolated."""

    def __init__(
        self,
        external_execution_id: str,
        client: AgentsRuntimeClient | None,
        configuration_error: str | None = None,
    ) -> None:
        self.external_execution_id = external_execution_id
        self._client = client
        self.execution_id: str | None = None
        self.delivery_failed = configuration_error is not None
        self._model_ordinal = 0
        self._tool_ordinal = 0
        self._tool_starts: dict[str, tuple[int, float]] = {}

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def workflow_started(self) -> None:
        if self._client is None:
            return
        self.execution_id = self._client.create_execution(self.external_execution_id)
        if self.execution_id is None:
            self.delivery_failed = True
            return
        self._append_step("STARTED")

    def model_completed(self, message: AssistantMessage) -> None:
        self._model_ordinal += 1
        usage = _safe_usage(message.usage)
        attributes: dict[str, Any] = {
            "ordinal": self._model_ordinal,
            "model": message.model,
            "status": "COMPLETED",
        }
        if message.stop_reason:
            attributes["stop_reason"] = message.stop_reason
        if usage:
            attributes["usage"] = usage
        self._append(
            "MODEL_CALL",
            actor_type="MODEL",
            actor_id=str(message.model),
            attributes=attributes,
            key=f"model-{self._model_ordinal}",
        )

    def tool_started(self, tool_use_id: str) -> None:
        self._tool_ordinal += 1
        self._tool_starts[tool_use_id] = (self._tool_ordinal, time.monotonic())

    def tool_completed(self, tool_use_id: str, succeeded: bool) -> None:
        ordinal, started = self._tool_starts.pop(
            tool_use_id, (self._tool_ordinal + 1, time.monotonic())
        )
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        tool_hash = hashlib.sha256(tool_use_id.encode("utf-8")).hexdigest()[:24]
        attributes: dict[str, Any] = {
            "ordinal": ordinal,
            "provider_tool_name": POLICY_TOOL_NAME,
            "status": "SUCCEEDED" if succeeded else "FAILED",
            "latency_ms": duration_ms,
            "tool_use_id_hash": tool_hash,
        }
        if not succeeded:
            attributes["error_category"] = "tool_failure"
        self._append(
            "TOOL_CALL",
            actor_type="TOOL",
            actor_id="lookup_policy",
            attributes=attributes,
            key=f"tool-{tool_hash}",
        )

    def workflow_completed(self) -> None:
        self._append_step("COMPLETED")
        self._complete("SUCCEEDED")

    def workflow_failed(self) -> None:
        self._append_step("FAILED")
        self._complete("FAILED")

    def _append_step(self, status: str) -> None:
        step_id = hashlib.sha256(
            f"{self.external_execution_id}:{AGENT_ID}".encode("utf-8")
        ).hexdigest()[:24]
        self._append(
            "WORKFLOW_STEP",
            actor_type="AGENT",
            actor_id=AGENT_ID,
            attributes={"status": status},
            key=f"workflow-step-{status.lower()}",
            step_id=f"step-{step_id}",
            step_name=AGENT_NAME,
            lifecycle=status,
        )

    def _append(
        self,
        event_type: str,
        *,
        actor_type: str,
        actor_id: str,
        attributes: dict[str, Any],
        key: str,
        step_id: str | None = None,
        step_name: str | None = None,
        lifecycle: str | None = None,
    ) -> None:
        if self._client is None or self.execution_id is None:
            return
        payload: dict[str, Any] = {
            "event_type": event_type,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "attributes": attributes,
            "idempotency_key": f"{self.external_execution_id}:{key}",
            "correlation_id": self.external_execution_id,
        }
        if event_type == "WORKFLOW_STEP":
            payload.update(
                {
                    "step_id": step_id,
                    "step_name": step_name,
                    "lifecycle": lifecycle,
                    "source_kind": SOURCE_KIND,
                }
            )
        delivered = self._client.append_event(
            self.execution_id,
            payload,
        )
        if not delivered:
            self.delivery_failed = True

    def _complete(self, status: str) -> None:
        if self._client is None or self.execution_id is None:
            return
        if not self._client.complete_execution(self.execution_id, status):
            self.delivery_failed = True


def create_policy_hooks(reporter: EvidenceReporter) -> dict[str, list[HookMatcher]]:
    """Observe the policy MCP tool without changing its inputs or result."""

    async def pre_tool_use(input_data: Any, tool_use_id: str, context: Any) -> dict[str, Any]:
        del input_data, context
        reporter.tool_started(tool_use_id)
        return {}

    async def post_tool_use(input_data: Any, tool_use_id: str, context: Any) -> dict[str, Any]:
        del input_data, context
        reporter.tool_completed(tool_use_id, succeeded=True)
        return {}

    async def post_tool_use_failure(
        input_data: Any, tool_use_id: str, context: Any
    ) -> dict[str, Any]:
        del input_data, context
        reporter.tool_completed(tool_use_id, succeeded=False)
        return {}

    matcher = f"^{POLICY_TOOL_NAME}$"
    return {
        "PreToolUse": [HookMatcher(matcher=matcher, hooks=[pre_tool_use])],
        "PostToolUse": [HookMatcher(matcher=matcher, hooks=[post_tool_use])],
        "PostToolUseFailure": [
            HookMatcher(matcher=matcher, hooks=[post_tool_use_failure])
        ],
    }


def create_reporter(session_id: str) -> EvidenceReporter:
    try:
        settings = GovernanceSettings.from_environment()
    except ValueError as error:
        return EvidenceReporter(
            external_execution_id=session_id,
            client=None,
            configuration_error=str(error),
        )
    return EvidenceReporter(
        external_execution_id=session_id,
        client=AgentsRuntimeClient(settings) if settings else None,
    )


def new_session_id() -> str:
    """A public Claude query session id, reused as the evidence correlation id."""
    return str(uuid.uuid4())


def _safe_usage(usage: dict[str, Any] | None) -> dict[str, int]:
    if not usage:
        return {}
    allowed = {
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
    return {
        key: value
        for key, value in usage.items()
        if key in allowed and isinstance(value, int) and not isinstance(value, bool)
    }
