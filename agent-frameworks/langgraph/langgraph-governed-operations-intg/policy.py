"""Protected-tool authorization adapters for the LangGraph developer lab."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from operations import OperationRequest


class AuthorizationOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class AuthorizationDecision:
    """A normalized result consumed by the protected database-write boundary."""

    outcome: AuthorizationOutcome
    reason_code: str
    source: str
    decision_reference: str | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome is AuthorizationOutcome.ALLOW


class OperationAuthorizer(Protocol):
    """The single authorization boundary used by the protected tool."""

    def authorize(self, request: OperationRequest) -> AuthorizationDecision: ...


class UnsupportedOperationAuthorizer:
    """Fail closed when a protected tool has no configured AIGP adapter."""

    def authorize(self, request: OperationRequest) -> AuthorizationDecision:
        del request
        return AuthorizationDecision(
            outcome=AuthorizationOutcome.UNAVAILABLE,
            reason_code="AIGP_RUNTIME_AUTHORIZATION_NOT_CONFIGURED",
            source="aigp-runtime-authorization-unconfigured",
        )


class AIGPRuntimeActionAuthorizer:
    """Use the OSS public contract immediately before the SQLite mutation.

    The control plane receives only the canonical AIGP execution ID and the
    bounded facts needed for this operation.  The LangGraph run ID is used for
    idempotency and correlation; a thread ID is never an execution identity.
    """

    def __init__(
        self,
        *,
        base_url: str,
        organization_id: str,
        project_id: str,
        expected_actor_id: str,
        access_token: str | None = None,
        development_actor_id: str | None = None,
    ) -> None:
        if not expected_actor_id:
            raise ValueError("expected_actor_id is required for protected tools.")
        self._base_url = base_url
        self._organization_id = organization_id
        self._project_id = project_id
        self._expected_actor_id = expected_actor_id
        self._access_token = access_token
        self._development_actor_id = development_actor_id

    def authorize(self, request: OperationRequest) -> AuthorizationDecision:
        if request.governance_execution_id is None:
            return AuthorizationDecision(
                outcome=AuthorizationOutcome.UNAVAILABLE,
                reason_code="AIGP_EXECUTION_REGISTRATION_UNAVAILABLE",
                source="aigp-runtime-action-authorization",
            )

        try:
            contracts = _runtime_authorization_contracts()
        except RuntimeError:
            return AuthorizationDecision(
                outcome=AuthorizationOutcome.UNAVAILABLE,
                reason_code="AUTHORIZATION_UNAVAILABLE",
                source="aigp-runtime-action-authorization",
            )
        try:
            authorization_request = contracts.request_type(
                execution_id=request.governance_execution_id,
                correlation_id=request.correlation_id,
                action_id="operations.update_status",
                resource=contracts.resource_type(
                    "sqlite.row", f"operational-record:{request.record_id}"
                ),
                context={
                    "intent": request.intent.value,
                    "requested_status": request.requested_status,
                },
                idempotency_key=(
                    f"{request.langgraph_run_id}:authorize:"
                    f"operations.update_status:{request.record_id}"
                ),
            )
            client = contracts.client_type(
                self._base_url,
                bearer_token=self._access_token,
                scope_headers=self._scope_headers(request),
            )
            captured = _CapturingAuthorizationClient(client)
            authorizer = contracts.enforcer_type(
                captured, actor_id=self._expected_actor_id
            )
            decision = authorizer.require_allow(authorization_request)
        except contracts.blocked_exception_type as error:
            captured_decision = captured.decision
            if (
                captured_decision is not None
                and captured_decision.outcome.value == AuthorizationOutcome.DENY.value
            ):
                return _decision_from_aigp(captured_decision)
            return AuthorizationDecision(
                outcome=AuthorizationOutcome.UNAVAILABLE,
                reason_code=getattr(error, "reason_code", "AUTHORIZATION_UNAVAILABLE"),
                source="aigp-runtime-action-authorization",
            )
        except (TypeError, ValueError):
            return AuthorizationDecision(
                outcome=AuthorizationOutcome.UNAVAILABLE,
                reason_code="INVALID_AUTHORIZATION_CONFIGURATION",
                source="aigp-runtime-action-authorization",
            )
        return _decision_from_aigp(decision)

    def _scope_headers(self, request: OperationRequest) -> dict[str, str]:
        headers = {
            "X-AI-Governance-Organization-Id": self._organization_id,
            "X-AI-Governance-Project-Id": self._project_id,
            "X-Correlation-Id": request.correlation_id,
            "X-Request-Id": f"{request.langgraph_run_id}:authorize",
        }
        if self._development_actor_id:
            headers["X-AI-Governance-Actor-Id"] = self._development_actor_id
        return headers


class SimulatedOperationAuthorizer:
    """Deterministic lab fixture; it is never a BHANUJ AIGP integration."""

    def __init__(self, outcome: AuthorizationOutcome) -> None:
        self._outcome = outcome

    def authorize(self, request: OperationRequest) -> AuthorizationDecision:
        decision_reference = f"simulated:{request.external_execution_id}:database.write"
        reason_code = {
            AuthorizationOutcome.ALLOW: "SIMULATED_ALLOW_DATABASE_WRITE",
            AuthorizationOutcome.DENY: "SIMULATED_DENY_DATABASE_WRITE",
            AuthorizationOutcome.UNAVAILABLE: "SIMULATED_AUTHORIZER_OUTAGE",
        }[self._outcome]
        return AuthorizationDecision(
            outcome=self._outcome,
            reason_code=reason_code,
            source="simulated-lab-fixture",
            decision_reference=decision_reference,
        )


# Backwards-compatible name for existing educational tests. New guide code uses
# SimulatedOperationAuthorizer to avoid suggesting a production policy fixture.
FixtureOperationAuthorizer = SimulatedOperationAuthorizer


@dataclass(frozen=True)
class _RuntimeAuthorizationContracts:
    client_type: type[Any]
    enforcer_type: type[Any]
    request_type: type[Any]
    resource_type: type[Any]
    blocked_exception_type: type[Exception]


class _CapturingAuthorizationClient:
    """Retain a DENY returned by the real client for deterministic handling."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.decision: Any | None = None

    def authorize(self, request: Any) -> Any:
        self.decision = self._client.authorize(request)
        return self.decision


def _runtime_authorization_contracts() -> _RuntimeAuthorizationContracts:
    """Load the OSS SDK lazily so offline fixture tests need no OSS install."""

    try:
        from ai_governance.runtime_authorization import (
            AIGPRuntimeActionAuthorizationClient,
            LangGraphOperationAuthorizer,
            ProtectedOperationBlocked,
            RuntimeActionAuthorizationRequest,
            RuntimeActionResource,
        )
    except ImportError as error:
        raise RuntimeError(
            "Install a compatible ai-governance-oss checkout before selecting "
            "the authoritative AIGP authorization adapter."
        ) from error
    return _RuntimeAuthorizationContracts(
        client_type=AIGPRuntimeActionAuthorizationClient,
        enforcer_type=LangGraphOperationAuthorizer,
        request_type=RuntimeActionAuthorizationRequest,
        resource_type=RuntimeActionResource,
        blocked_exception_type=ProtectedOperationBlocked,
    )


def _decision_from_aigp(decision: Any) -> AuthorizationDecision:
    outcome = (
        AuthorizationOutcome.ALLOW
        if decision.outcome.value == AuthorizationOutcome.ALLOW.value
        else AuthorizationOutcome.DENY
    )
    return AuthorizationDecision(
        outcome=outcome,
        reason_code=str(decision.reason_code),
        source="aigp-runtime-action-authorization",
        decision_reference=str(decision.decision_id),
    )
