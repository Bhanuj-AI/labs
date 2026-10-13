"""Verified BHANUJ Agents Runtime evidence adapter for the LangGraph lab."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from policy import AuthorizationDecision


AGENT_ID = "langgraph-governed-operations"
AGENT_NAME = "LangGraph governed operations assistant"
AGENT_VERSION = "1.0.0"
RUNTIME_PROVIDER = "langgraph"
EVENT_SCHEMA_VERSION = "1"
MAX_DELIVERY_ATTEMPTS = 3


class EvidenceStatus(str, Enum):
    DISABLED = "DISABLED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    DEGRADED = "DEGRADED"


class GovernanceConfigurationError(ValueError):
    """The opt-in runtime evidence configuration is incomplete."""


class GovernanceDeliveryError(RuntimeError):
    """A public Agents Runtime request could not be safely delivered."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class GovernanceSettings:
    """Settings that map directly to the public REST scope headers."""

    base_url: str
    organization_id: str
    project_id: str
    actor_id: str | None = None
    access_token: str | None = None

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
            raise GovernanceConfigurationError(
                "BHANUJ runtime evidence requires " + ", ".join(missing)
            )
        return cls(
            base_url=str(values["AI_GOVERNANCE_BASE_URL"]).rstrip("/"),
            organization_id=str(values["AI_GOVERNANCE_ORGANIZATION_ID"]),
            project_id=str(values["AI_GOVERNANCE_PROJECT_ID"]),
            actor_id=os.getenv("AI_GOVERNANCE_ACTOR_ID"),
            access_token=os.getenv("AI_GOVERNANCE_ACCESS_TOKEN"),
        )


Transport = Callable[[str, Mapping[str, str], dict[str, Any]], dict[str, Any]]
Sleep = Callable[[float], None]


class FaultInjectingTransport:
    """Lab-only transport fault injector around the same REST client path.

    It is used by the hands-on failure scenarios and unit tests.  It never
    changes the execution architecture or supplies a BHANUJ response.
    """

    def __init__(self, delegate: Transport, *, fail_stage: str) -> None:
        if fail_stage not in {"registration", "evidence"}:
            raise ValueError("fail_stage must be 'registration' or 'evidence'.")
        self._delegate = delegate
        self._fail_stage = fail_stage

    def __call__(
        self, url: str, headers: Mapping[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
        is_registration = url.rstrip("/").endswith("/api/v1/agent-executions")
        is_evidence = url.rstrip("/").endswith("/events")
        if self._fail_stage == "registration" and is_registration:
            raise GovernanceDeliveryError(
                "Simulated execution registration failure.", retryable=False
            )
        if self._fail_stage == "evidence" and is_evidence:
            raise GovernanceDeliveryError(
                "Simulated runtime evidence outage.", retryable=True
            )
        return self._delegate(url, headers, payload)


class AgentsRuntimeClient:
    """Small retrying client for the verified Agents Runtime REST contract."""

    def __init__(
        self,
        settings: GovernanceSettings,
        *,
        transport: Transport | None = None,
        sleep: Sleep = time.sleep,
        max_attempts: int = MAX_DELIVERY_ATTEMPTS,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one.")
        self._settings = settings
        self._transport = transport or self._post_json
        self._sleep = sleep
        self._max_attempts = max_attempts

    def start_execution(
        self,
        *,
        external_execution_id: str,
        correlation_id: str,
        metadata: dict[str, object],
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
                "metadata": metadata,
            },
            correlation_id=correlation_id,
            request_id=f"{external_execution_id}:start",
        )
        try:
            return str(response["execution"]["execution_id"])
        except (KeyError, TypeError) as error:
            raise GovernanceDeliveryError(
                "Agents Runtime returned no execution ID."
            ) from error

    def append_event(
        self,
        execution_id: str,
        payload: dict[str, Any],
        *,
        correlation_id: str,
        request_id: str,
    ) -> str:
        response = self._post(
            f"/api/v1/agent-executions/{execution_id}/events",
            payload,
            correlation_id=correlation_id,
            request_id=request_id,
        )
        try:
            return str(response["event"]["event_id"])
        except (KeyError, TypeError) as error:
            raise GovernanceDeliveryError("Agents Runtime returned no event ID.") from error

    def complete_execution(
        self,
        execution_id: str,
        *,
        status: str,
        correlation_id: str,
        request_id: str,
    ) -> None:
        self._post(
            f"/api/v1/agent-executions/{execution_id}/complete",
            {"status": status},
            correlation_id=correlation_id,
            request_id=request_id,
        )

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        correlation_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "X-AI-Governance-Organization-Id": self._settings.organization_id,
            "X-AI-Governance-Project-Id": self._settings.project_id,
            "X-Correlation-Id": correlation_id,
            "X-Request-Id": request_id,
        }
        if self._settings.actor_id:
            headers["X-AI-Governance-Actor-Id"] = self._settings.actor_id
        if self._settings.access_token:
            headers["Authorization"] = f"Bearer {self._settings.access_token}"

        last_error: GovernanceDeliveryError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._transport(
                    f"{self._settings.base_url}{path}", headers, payload
                )
            except GovernanceDeliveryError as error:
                last_error = error
                if not error.retryable or attempt == self._max_attempts:
                    raise
                self._sleep(0.1 * attempt)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _post_json(
        url: str, headers: Mapping[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:  # noqa: S310 - configured URL
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise GovernanceDeliveryError(
                f"Agents Runtime rejected delivery with HTTP {error.code}.",
                retryable=error.code in {408, 429, 500, 502, 503, 504},
            ) from error
        except (URLError, TimeoutError) as error:
            raise GovernanceDeliveryError(
                f"Agents Runtime delivery failed: {error}", retryable=True
            ) from error
        except json.JSONDecodeError as error:
            raise GovernanceDeliveryError(
                "Agents Runtime returned invalid JSON."
            ) from error


class ExecutionEvidenceReporter:
    """Observe LangGraph facts through the public, one-way evidence API.

    A failed evidence delivery degrades reporting and stops later delivery.  It
    never fabricates a terminal record, and it never changes the LangGraph
    operation outcome.
    """

    def __init__(
        self,
        client: AgentsRuntimeClient | None,
        *,
        external_execution_id: str,
        correlation_id: str,
    ) -> None:
        self._client = client
        self.external_execution_id = external_execution_id
        self.correlation_id = correlation_id
        self.execution_id: str | None = None
        self.status = EvidenceStatus.DISABLED if client is None else EvidenceStatus.RUNNING
        self.error: str | None = None

    @classmethod
    def from_environment(
        cls,
        *,
        external_execution_id: str,
        correlation_id: str,
        transport: Transport | None = None,
        sleep: Sleep = time.sleep,
        max_attempts: int = MAX_DELIVERY_ATTEMPTS,
    ) -> ExecutionEvidenceReporter:
        try:
            settings = GovernanceSettings.from_environment()
        except GovernanceConfigurationError as error:
            reporter = cls(
                None,
                external_execution_id=external_execution_id,
                correlation_id=correlation_id,
            )
            reporter.status = EvidenceStatus.DEGRADED
            reporter.error = str(error)
            return reporter
        return cls(
            AgentsRuntimeClient(
                settings,
                transport=transport,
                sleep=sleep,
                max_attempts=max_attempts,
            )
            if settings is not None
            else None,
            external_execution_id=external_execution_id,
            correlation_id=correlation_id,
        )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def start(self, metadata: dict[str, object]) -> None:
        if not self.enabled:
            return
        try:
            self.execution_id = self._client.start_execution(
                external_execution_id=self.external_execution_id,
                correlation_id=self.correlation_id,
                metadata=metadata,
            )
        except GovernanceDeliveryError as error:
            self._degrade(error)

    def workflow_step(
        self,
        step_name: str,
        lifecycle: str,
        *,
        attributes: dict[str, object] | None = None,
    ) -> str | None:
        step_id = f"{self.external_execution_id}:node:{step_name}"
        return self._append(
            f"workflow-step:{step_name}:{lifecycle.lower()}",
            {
                "event_type": "WORKFLOW_STEP",
                "step_id": step_id,
                "step_name": step_name,
                "lifecycle": lifecycle,
                "source_kind": "langgraph.node",
                "idempotency_key": f"{step_id}:{lifecycle.lower()}",
                "attributes": attributes or {},
            },
        )

    def tool_completed(
        self,
        tool_call_id: str,
        *,
        outcome: str,
        resource_reference: str,
        causation_id: str | None,
    ) -> str | None:
        return self._tool_event(
            tool_call_id,
            outcome=outcome,
            resource_reference=resource_reference,
            causation_id=causation_id,
        )

    def governance_decision(
        self,
        decision: AuthorizationDecision,
        *,
        causation_id: str | None = None,
    ) -> str | None:
        decision_key = f"governance-decision:{decision.source}:{decision.outcome.value.lower()}"
        return self._append(
            decision_key,
            {
                "event_type": "GOVERNANCE_DECISION",
                "idempotency_key": f"{self.external_execution_id}:{decision_key}",
                "causation_id": causation_id,
                "actor_id": "governance-adapter",
                "actor_type": "GOVERNANCE",
                "attributes": {
                    "decision_outcome": decision.outcome.value,
                    "reason_code": decision.reason_code,
                    "decision_source": decision.source,
                    "decision_reference": decision.decision_reference,
                    "event_schema_version": EVENT_SCHEMA_VERSION,
                },
            },
        )

    def error_event(
        self,
        error_class: str,
        *,
        reason_code: str,
        causation_id: str | None = None,
    ) -> str | None:
        return self._append(
            f"error:{error_class}",
            {
                "event_type": "ERROR",
                "idempotency_key": f"{self.external_execution_id}:error:{error_class}",
                "causation_id": causation_id,
                "actor_id": AGENT_ID,
                "actor_type": "AGENT",
                "attributes": {
                    "error_class": error_class,
                    "reason_code": reason_code,
                    "event_schema_version": EVENT_SCHEMA_VERSION,
                },
            },
        )

    def finalize(self, status: str) -> None:
        if not self._can_deliver():
            return
        try:
            self._client.complete_execution(
                self.execution_id,
                status=status,
                correlation_id=self.correlation_id,
                request_id=f"{self.external_execution_id}:complete:{status.lower()}",
            )
            self.status = EvidenceStatus.COMPLETED
        except GovernanceDeliveryError as error:
            self._degrade(error)

    def _tool_event(
        self,
        tool_call_id: str,
        *,
        resource_reference: str,
        outcome: str,
        causation_id: str | None = None,
    ) -> str | None:
        """Record one accepted evidence event for one actual tool invocation.

        The OSS runtime makes ``runtime_tool_call_id`` unique within an
        execution.  A request and later result therefore cannot be represented
        as two TOOL_CALL events for one native invocation.  The lab emits the
        completed invocation once, with its bounded outcome, and represents the
        preceding simulated authorization separately as GOVERNANCE_DECISION.
        """
        payload: dict[str, Any] = {
            "event_type": "TOOL_CALL",
            "idempotency_key": f"{self.external_execution_id}:tool:{tool_call_id}",
            "causation_id": causation_id,
            "actor_id": "operations-database",
            "actor_type": "TOOL",
            "tool_call_context": {
                "schema_version": EVENT_SCHEMA_VERSION,
                "runtime_tool_call_id": tool_call_id,
                "tool_call_group_id": f"{self.external_execution_id}:database-write",
                "depends_on_tool_call_ids": [],
            },
            "resource_references": [resource_reference],
            "attributes": {
                "tool_name": "operations.update_status",
                "operation": "database.write",
                "outcome": outcome,
                "event_schema_version": EVENT_SCHEMA_VERSION,
            },
        }
        return self._append(f"tool:{tool_call_id}", payload)

    def _append(self, event_name: str, payload: dict[str, Any]) -> str | None:
        if not self._can_deliver():
            return None
        try:
            return self._client.append_event(
                self.execution_id,
                payload,
                correlation_id=self.correlation_id,
                request_id=f"{self.external_execution_id}:{event_name}",
            )
        except GovernanceDeliveryError as error:
            self._degrade(error)
            return None

    def _can_deliver(self) -> bool:
        return (
            self._client is not None
            and self.execution_id is not None
            and self.status is EvidenceStatus.RUNNING
        )

    def _degrade(self, error: GovernanceDeliveryError) -> None:
        self.status = EvidenceStatus.DEGRADED
        self.error = str(error)
