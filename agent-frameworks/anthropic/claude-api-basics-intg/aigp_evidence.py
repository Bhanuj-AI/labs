"""Optional, bounded AIGP Agent Executions API adapter.

The application remains the execution plane.  This adapter sends identifiers,
statuses, model/tool metadata, and timing only; it never serializes Claude
messages, response text, tool arguments/results, credentials, or raw SDK
objects.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Settings:
    base_url: str
    organization_id: str
    project_id: str
    access_token: str | None

    @classmethod
    def from_environment(cls) -> "Settings | None":
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
            raise ValueError("AIGP evidence requires " + ", ".join(missing))
        return cls(
            base_url=str(values["AI_GOVERNANCE_BASE_URL"]).rstrip("/"),
            organization_id=str(values["AI_GOVERNANCE_ORGANIZATION_ID"]),
            project_id=str(values["AI_GOVERNANCE_PROJECT_ID"]),
            access_token=os.getenv("AI_GOVERNANCE_ACCESS_TOKEN") or None,
        )


class EvidenceReporter:
    """Best-effort evidence delivery for non-protected Claude examples."""

    def __init__(self, external_execution_id: str, settings: Settings | None) -> None:
        self.external_execution_id = external_execution_id
        self._settings = settings
        self.execution_id: str | None = None
        self.delivery_failed = False

    @property
    def enabled(self) -> bool:
        return self._settings is not None

    @property
    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.execution_id and not self.delivery_failed:
            return "delivered"
        return "unavailable (Claude execution was not affected)"

    def start(self, *, agent_id: str, agent_name: str, workflow: str) -> None:
        response = self._post(
            "/api/v1/agent-executions",
            {
                "external_execution_id": self.external_execution_id,
                "runtime_provider": "anthropic_messages_api",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "agent_version": "1.0.0",
                "correlation_id": self.external_execution_id,
                "metadata": {"workflow": workflow},
            },
        )
        execution = response.get("execution") if response else None
        if isinstance(execution, dict) and isinstance(execution.get("execution_id"), str):
            self.execution_id = execution["execution_id"]
        elif self.enabled:
            self.delivery_failed = True

    def event(
        self,
        event_type: str,
        *,
        actor_type: str,
        actor_id: str,
        attributes: dict[str, Any],
        key: str,
        tool_call_context: dict[str, Any] | None = None,
    ) -> None:
        if self.execution_id is None:
            return
        payload: dict[str, Any] = {
            "event_type": event_type,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "attributes": attributes,
            "idempotency_key": f"{self.external_execution_id}:{key}",
            "correlation_id": self.external_execution_id,
        }
        if tool_call_context is not None:
            payload["tool_call_context"] = tool_call_context
        if self._post(f"/api/v1/agent-executions/{self.execution_id}/events", payload) is None:
            self.delivery_failed = True

    def complete(self, status: str) -> None:
        if self.execution_id is None:
            return
        if self._post(
            f"/api/v1/agent-executions/{self.execution_id}/complete", {"status": status}
        ) is None:
            self.delivery_failed = True

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        if self._settings is None:
            return None
        headers = {
            "Content-Type": "application/json",
            "X-AI-Governance-Organization-Id": self._settings.organization_id,
            "X-AI-Governance-Project-Id": self._settings.project_id,
        }
        if self._settings.access_token:
            headers["Authorization"] = f"Bearer {self._settings.access_token}"
        request = urllib.request.Request(
            f"{self._settings.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                data = response.read().decode("utf-8")
                return json.loads(data) if data else {}
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
            return None


def new_execution_id() -> str:
    return str(uuid.uuid4())
