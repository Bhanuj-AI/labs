"""Runtime-owned, serialisable contracts with no Core payload fields."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

REPLAY_INTERVENTION_ENVELOPE_SCHEMA_VERSION = "replay-intervention-envelope/v1"


class Scenario(StrEnum):
    ALIGNED = "aligned"
    IGNORED = "ignored"


class Decision(StrEnum):
    ALLOW = "ALLOW"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


OUTCOME_SCORES: Mapping[Decision, float] = {
    Decision.ALLOW: 0.0,
    Decision.REVIEW: 0.5,
    Decision.BLOCK: 1.0,
}


@dataclass(frozen=True)
class CounterfactualProfile:
    reference: str
    evidence: Mapping[str, Any]
    digest: str


@dataclass(frozen=True)
class ObservedExecution:
    external_execution_id: str
    replay_reference: str
    runtime_tool_call_id: str
    evidence_reference: str
    evidence_digest: str
    scenario: Scenario
    decision: Decision
    outcome_score: float
    model_id: str
    inference_configuration: Mapping[str, object]

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ReplayEnvelope:
    """The bounded envelope received from the Core replay adapter."""

    policy_id: str
    policy_version: int
    external_execution_id: str
    runtime_tool_call_id: str
    intervention_provider: str
    intervention_provider_version: str
    strategy: str
    original_evidence_digest: str
    counterfactual_reference: str
    counterfactual_digest: str
    intervention_digest: str
    schema_version: str = REPLAY_INTERVENTION_ENVELOPE_SCHEMA_VERSION


@dataclass(frozen=True)
class ReplayResult:
    replay_reference: str
    source_external_execution_id: str
    source_runtime_tool_call_id: str
    replay_external_execution_id: str
    replay_runtime_tool_call_id: str
    counterfactual_evidence_digest: str
    decision: Decision
    outcome_score: float
    outcome_ref: str

    def public_dict(self) -> dict[str, object]:
        return {
            "execution_status": "COMPLETED",
            "isolated": True,
            "replay_reference": self.replay_reference,
            "source_external_execution_id": self.source_external_execution_id,
            "source_runtime_tool_call_id": self.source_runtime_tool_call_id,
            "replay_external_execution_id": self.replay_external_execution_id,
            "replay_runtime_tool_call_id": self.replay_runtime_tool_call_id,
            "counterfactual_evidence_digest": self.counterfactual_evidence_digest,
            "outcome_score": self.outcome_score,
            "outcome_ref": self.outcome_ref,
        }
