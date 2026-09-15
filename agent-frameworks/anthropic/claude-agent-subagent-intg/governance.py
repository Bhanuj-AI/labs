"""Fail-open BHANUJ Platform evidence for a public Claude SDK subagent run.

No prompt, response, reasoning, tool input, tool output, or raw SDK message is
sent to the evidence plane. Claude Agent SDK remains entirely responsible for
agent execution and exception behaviour.
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
MAIN_AGENT_ID = "claude-policy-validation-main-agent"
MAIN_AGENT_NAME = "Main Agent"
SUBAGENT_TYPE = "research-policy-subagent"
SUBAGENT_NAME = "Research Subagent"
AGENT_VERSION = "1.0.0"
SESSION_SOURCE_KIND = "claude_agent_sdk.session"
SUBAGENT_SOURCE_KIND = "claude_agent_sdk.subagent"
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
    """The existing public BHANUJ OSS Agent Executions REST contract."""

    def __init__(self, settings: GovernanceSettings) -> None:
        self._settings = settings

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        request = urllib.request.Request(
            f"{self._settings.base_url}{path}",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-AI-Governance-Organization-Id": self._settings.organization_id,
                "X-AI-Governance-Project-Id": self._settings.project_id,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                body = response.read().decode("utf-8")
                decoded = json.loads(body) if body else {}
                return decoded if isinstance(decoded, dict) else None
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            OSError,
            TimeoutError,
            ValueError,
        ):
            return None

    def start_execution(self, external_execution_id: str) -> str | None:
        response = self._post(
            "/api/v1/agent-executions",
            {
                "agent_id": MAIN_AGENT_ID,
                "agent_name": MAIN_AGENT_NAME,
                "agent_version": AGENT_VERSION,
                "external_execution_id": external_execution_id,
                "runtime_provider": RUNTIME_PROVIDER,
                "correlation_id": external_execution_id,
                "metadata": {"workflow": "policy_validation_subagent"},
            },
        )
        execution = response.get("execution") if response else None
        execution_id = execution.get("execution_id") if isinstance(execution, dict) else None
        return str(execution_id) if execution_id else None

    def append_event(self, execution_id: str, payload: dict[str, Any]) -> bool:
        return self._post(f"/api/v1/agent-executions/{execution_id}/events", payload) is not None

    def complete_execution(self, execution_id: str, status: str) -> bool:
        return self._post(
            f"/api/v1/agent-executions/{execution_id}/complete", {"status": status}
        ) is not None


class EvidenceReporter:
    """Captures bounded facts and isolates every evidence-delivery failure."""

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
        self._tool_starts: dict[str, tuple[int, float, bool]] = {}
        self._subagent_steps: dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def workflow_started(self) -> None:
        if self._client is None:
            return
        self.execution_id = self._client.start_execution(self.external_execution_id)
        if self.execution_id is None:
            self.delivery_failed = True
            return
        self._append_workflow_step(
            MAIN_AGENT_ID,
            MAIN_AGENT_NAME,
            self._stable_step_id(MAIN_AGENT_ID),
            "STARTED",
            SESSION_SOURCE_KIND,
        )

    def subagent_started(self, agent_id: str) -> None:
        step_id = self._stable_step_id(agent_id)
        self._subagent_steps[agent_id] = step_id
        self._append_workflow_step(
            SUBAGENT_TYPE,
            SUBAGENT_NAME,
            step_id,
            "STARTED",
            SUBAGENT_SOURCE_KIND,
        )

    def subagent_completed(self, agent_id: str) -> None:
        step_id = self._subagent_steps.pop(agent_id, self._stable_step_id(agent_id))
        self._append_workflow_step(
            SUBAGENT_TYPE,
            SUBAGENT_NAME,
            step_id,
            "COMPLETED",
            SUBAGENT_SOURCE_KIND,
        )

    def model_completed(self, message: AssistantMessage) -> None:
        self._model_ordinal += 1
        is_subagent = message.parent_tool_use_id is not None
        attributes: dict[str, Any] = {
            "ordinal": self._model_ordinal,
            "model": message.model,
            "status": "COMPLETED",
            "agent_scope": "research_subagent" if is_subagent else "main_agent",
        }
        if message.stop_reason:
            attributes["stop_reason"] = message.stop_reason
        usage = _safe_usage(message.usage)
        if usage:
            attributes["usage"] = usage
        self._append(
            "MODEL_CALL",
            actor_type="MODEL",
            actor_id=str(message.model),
            attributes=attributes,
            key=f"model-{self._model_ordinal}",
        )

    def tool_started(self, tool_use_id: str, inside_subagent: bool) -> None:
        self._tool_ordinal += 1
        self._tool_starts[tool_use_id] = (
            self._tool_ordinal,
            time.monotonic(),
            inside_subagent,
        )

    def is_active_subagent(self, agent_id: object) -> bool:
        """Check ephemeral lifecycle state without retaining it as evidence."""
        return isinstance(agent_id, str) and agent_id in self._subagent_steps

    def tool_completed(self, tool_use_id: str, succeeded: bool) -> None:
        ordinal, started, inside_subagent = self._tool_starts.pop(
            tool_use_id, (self._tool_ordinal + 1, time.monotonic(), False)
        )
        tool_hash = hashlib.sha256(tool_use_id.encode("utf-8")).hexdigest()[:24]
        attributes: dict[str, Any] = {
            "ordinal": ordinal,
            "provider_tool_name": POLICY_TOOL_NAME,
            "status": "SUCCEEDED" if succeeded else "FAILED",
            "latency_ms": max(0, round((time.monotonic() - started) * 1000)),
            "tool_use_id_hash": tool_hash,
            "agent_scope": "research_subagent" if inside_subagent else "main_agent",
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
        self._append_workflow_step(
            MAIN_AGENT_ID,
            MAIN_AGENT_NAME,
            self._stable_step_id(MAIN_AGENT_ID),
            "COMPLETED",
            SESSION_SOURCE_KIND,
        )
        self._complete("SUCCEEDED")

    def workflow_failed(self) -> None:
        self._append_workflow_step(
            MAIN_AGENT_ID,
            MAIN_AGENT_NAME,
            self._stable_step_id(MAIN_AGENT_ID),
            "FAILED",
            SESSION_SOURCE_KIND,
        )
        self._complete("FAILED")

    def _append_workflow_step(
        self, actor_id: str, step_name: str, step_id: str, lifecycle: str, source_kind: str
    ) -> None:
        self._append(
            "WORKFLOW_STEP",
            actor_type="AGENT",
            actor_id=actor_id,
            attributes={"status": lifecycle},
            key=f"{step_id}:{lifecycle.lower()}",
            step_id=step_id,
            step_name=step_name,
            lifecycle=lifecycle,
            source_kind=source_kind,
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
        source_kind: str | None = None,
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
                    "source_kind": source_kind,
                }
            )
        if not self._client.append_event(self.execution_id, payload):
            self.delivery_failed = True

    def _complete(self, status: str) -> None:
        if self._client is not None and self.execution_id is not None:
            if not self._client.complete_execution(self.execution_id, status):
                self.delivery_failed = True

    def _stable_step_id(self, identifier: str) -> str:
        digest = hashlib.sha256(
            f"{self.external_execution_id}:{identifier}".encode("utf-8")
        ).hexdigest()[:24]
        return f"step-{digest}"


def create_hooks(reporter: EvidenceReporter) -> dict[str, list[HookMatcher]]:
    """Public Claude SDK hooks; callbacks never alter agent execution."""

    async def subagent_start(input_data: Any, tool_use_id: str | None, context: Any) -> dict[str, Any]:
        del tool_use_id, context
        reporter.subagent_started(str(input_data["agent_id"]))
        return {}

    async def subagent_stop(input_data: Any, tool_use_id: str | None, context: Any) -> dict[str, Any]:
        del tool_use_id, context
        reporter.subagent_completed(str(input_data["agent_id"]))
        return {}

    async def pre_tool_use(input_data: Any, tool_use_id: str, context: Any) -> dict[str, Any]:
        del context
        reporter.tool_started(tool_use_id, reporter.is_active_subagent(input_data.get("agent_id")))
        return {}

    async def post_tool_use(input_data: Any, tool_use_id: str, context: Any) -> dict[str, Any]:
        del input_data, context
        reporter.tool_completed(tool_use_id, succeeded=True)
        return {}

    async def post_tool_use_failure(input_data: Any, tool_use_id: str, context: Any) -> dict[str, Any]:
        del input_data, context
        reporter.tool_completed(tool_use_id, succeeded=False)
        return {}

    subagent_matcher = f"^{SUBAGENT_TYPE}$"
    tool_matcher = f"^{POLICY_TOOL_NAME}$"
    return {
        "SubagentStart": [HookMatcher(matcher=subagent_matcher, hooks=[subagent_start])],
        "SubagentStop": [HookMatcher(matcher=subagent_matcher, hooks=[subagent_stop])],
        "PreToolUse": [HookMatcher(matcher=tool_matcher, hooks=[pre_tool_use])],
        "PostToolUse": [HookMatcher(matcher=tool_matcher, hooks=[post_tool_use])],
        "PostToolUseFailure": [
            HookMatcher(matcher=tool_matcher, hooks=[post_tool_use_failure])
        ],
    }


def create_reporter(session_id: str) -> EvidenceReporter:
    try:
        settings = GovernanceSettings.from_environment()
    except ValueError as error:
        return EvidenceReporter(session_id, None, configuration_error=str(error))
    return EvidenceReporter(session_id, AgentsRuntimeClient(settings) if settings else None)


def new_session_id() -> str:
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
