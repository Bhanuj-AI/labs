"""The protected-tool boundary used by the LangGraph developer lab.

The OSS control plane currently exposes no public external operation-
authorization contract.  The simulated authorizer below exists only to test
the LangGraph boundary; it must never be described as a BHANUJ AIGP decision
service or security control.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

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
    """The adapter seam for a future public BHANUJ AIGP authorization contract."""

    def authorize(self, request: OperationRequest) -> AuthorizationDecision: ...


class UnsupportedOperationAuthorizer:
    """Fail closed until BHANUJ AIGP exposes a suitable public contract.

    It is deliberately an unavailable adapter, not an implementation of a
    remote BHANUJ AIGP client. It is the live-safe default for protected operations
    in this lab.
    """

    def authorize(self, request: OperationRequest) -> AuthorizationDecision:
        del request
        return AuthorizationDecision(
            outcome=AuthorizationOutcome.UNAVAILABLE,
            reason_code="PUBLIC_OPERATION_AUTHORIZATION_UNAVAILABLE",
            source="lab-contract-gap",
        )


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
