"""Fail-open evidence reporting for the AI Governance Agents Runtime API."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


AGENT_ID = "langgraph-insurance-claim-workflow"
AGENT_NAME = "LangGraph insurance claim workflow"
AGENT_VERSION = "1.0.0"
RUNTIME_PROVIDER = "langgraph"


class EvidenceStatus(str, Enum):
    """Local delivery state; this is not an Agents Runtime execution status."""

    DISABLED = "DISABLED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    DEGRADED = "DEGRADED"


class GovernanceDeliveryError(RuntimeError):
    """An Agents Runtime request could not be delivered or was rejected."""


@dataclass(frozen=True)
class GovernanceSettings:
    """Guide-side settings that map directly to documented REST request scope."""

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
        supplied = {name: value for name, value in values.items() if value}
        if not supplied:
            return None
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(
                "AI Governance instrumentation requires " + ", ".join(missing)
            )
        return cls(
            base_url=values["AI_GOVERNANCE_BASE_URL"].rstrip("/"),
            organization_id=values["AI_GOVERNANCE_ORGANIZATION_ID"],
            project_id=values["AI_GOVERNANCE_PROJECT_ID"],
        )


Transport = Callable[[str, Mapping[str, str], dict[str, Any]], dict[str, Any]]
StageNode = Callable[[dict[str, object]], dict[str, object]]


class AgentsRuntimeClient:
    """Small client for the public Agents Runtime execution-evidence API."""

    def __init__(self, settings: GovernanceSettings, transport: Transport | None = None):
        self._settings = settings
        self._transport = transport or self._post_json

    def start_execution(
        self, external_execution_id: str, correlation_id: str | None
    ) -> str:
        response = self._post(
            "/api/v1/agent-executions",
            {
                "agent_id": AGENT_ID,
                "agent_name": AGENT_NAME,
                "agent_version": AGENT_VERSION,
                "external_execution_id": external_execution_id,
                "runtime_provider": RUNTIME_PROVIDER,
                "correlation_id": correlation_id,
                "metadata": {"workflow": "insurance_claim"},
            },
        )
        try:
            return str(response["execution"]["execution_id"])
        except (KeyError, TypeError) as exc:
            raise GovernanceDeliveryError("Agents Runtime returned no execution ID.") from exc

    def append_workflow_step(
        self,
        execution_id: str,
        *,
        node_name: str,
        lifecycle: str,
        evidence: dict[str, object] | None = None,
    ) -> None:
        step_id = f"{execution_id}:node:{node_name}"
        self._post(
            f"/api/v1/agent-executions/{execution_id}/events",
            {
                "event_type": "WORKFLOW_STEP",
                "step_id": step_id,
                "step_name": node_name,
                "lifecycle": lifecycle,
                "source_kind": "langgraph.node",
                "idempotency_key": f"{step_id}:{lifecycle}",
                "attributes": evidence or {},
            },
        )

    def complete_execution(self, execution_id: str) -> None:
        self._post(
            f"/api/v1/agent-executions/{execution_id}/complete",
            {"status": "SUCCEEDED"},
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._transport(
            f"{self._settings.base_url}{path}",
            {
                "Content-Type": "application/json",
                "X-AI-Governance-Organization-Id": self._settings.organization_id,
                "X-AI-Governance-Project-Id": self._settings.project_id,
            },
            payload,
        )

    @staticmethod
    def _post_json(
        url: str, headers: Mapping[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:  # noqa: S310 - configured URL
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GovernanceDeliveryError(f"Agents Runtime delivery failed: {exc}") from exc


class ExecutionEvidenceReporter:
    """Observe one LangGraph run without influencing its business result."""

    def __init__(
        self,
        client: AgentsRuntimeClient | None,
        external_execution_id: str | None = None,
    ) -> None:
        self._client = client
        self.external_execution_id = external_execution_id or f"langgraph-{uuid4()}"
        self.execution_id: str | None = None
        self.status = EvidenceStatus.DISABLED if client is None else EvidenceStatus.RUNNING
        self.error: str | None = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @classmethod
    def from_environment(cls) -> ExecutionEvidenceReporter:
        try:
            settings = GovernanceSettings.from_environment()
        except ValueError as exc:
            reporter = cls(None)
            reporter.status = EvidenceStatus.DEGRADED
            reporter.error = str(exc)
            return reporter
        return cls(AgentsRuntimeClient(settings) if settings else None)

    def workflow_started(self, claim_id: str) -> None:
        if not self.enabled:
            return
        try:
            self.execution_id = self._client.start_execution(
                self.external_execution_id, correlation_id=claim_id
            )
        except GovernanceDeliveryError as exc:
            self._degrade(exc)

    def wrap_node(self, node_name: str, node: StageNode) -> StageNode:
        """Observe a node without changing its business logic or result."""

        def observed_node(state: dict[str, object]) -> dict[str, object]:
            self._record_workflow_step(node_name, "STARTED")
            result = node(state)
            self._record_workflow_step(
                node_name, "COMPLETED", _node_evidence(node_name, result)
            )
            return result

        return observed_node

    def workflow_completed(self) -> None:
        if not self._can_deliver():
            return
        try:
            self._client.complete_execution(self.execution_id)
            self.status = EvidenceStatus.SUCCEEDED
        except GovernanceDeliveryError as exc:
            self._degrade(exc)

    def _record_workflow_step(
        self, node_name: str, lifecycle: str, evidence: dict[str, object] | None = None
    ) -> None:
        if not self._can_deliver():
            return
        try:
            self._client.append_workflow_step(
                self.execution_id,
                node_name=node_name,
                lifecycle=lifecycle,
                evidence=evidence,
            )
        except GovernanceDeliveryError as exc:
            self._degrade(exc)

    def _can_deliver(self) -> bool:
        return (
            self._client is not None
            and self.execution_id is not None
            and self.status is EvidenceStatus.RUNNING
        )

    def _degrade(self, error: GovernanceDeliveryError) -> None:
        self.status = EvidenceStatus.DEGRADED
        self.error = str(error)


def _node_evidence(node_name: str, result: Mapping[str, object]) -> dict[str, object]:
    """Return bounded, explainable facts instead of raw claim payloads."""
    if node_name == "load_claim":
        return {"claim_loaded": True}
    if node_name == "check_policy":
        return {"policy_active": bool(result["policy_active"])}
    if node_name == "evaluate_evidence":
        return {"damage_verified": bool(result["damage_verified"])}
    if node_name == "make_decision":
        return {"decision": str(result["decision"])}
    return {}
