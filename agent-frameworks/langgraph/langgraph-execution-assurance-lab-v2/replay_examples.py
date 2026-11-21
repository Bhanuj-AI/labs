"""Small, inspectable fixtures for the LangGraph v2 evidence-replay tutorial."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bhanuj_governance_plugin_api import (
    EvidenceResolutionPlan,
    ExecutionReplayCapability,
    ReplayEvidenceBinding,
    ReplayInterventionBinding,
    ReplayToolCall,
    ReplayToolCallKind,
    ToolArgumentDigestAlgorithm,
    create_tool_argument_digest,
)
from aigp_langgraph_replay_adapter import (
    LangGraphToolCallBinding,
    LangGraphToolNodeReplaySession,
    PrivateToolEvidence,
    PrivateToolEvidenceStore,
)

MANIFEST_HASH = "a" * 64


@dataclass(frozen=True)
class EvidenceCall:
    """One observed call and the private evidence the runtime retained for it."""

    planned_call_id: str
    runtime_call_id: str
    tool_symbol: str
    arguments: Mapping[str, object]
    recorded_content: str
    occurrence: int = 1

    @property
    def evidence_reference(self) -> str:
        return f"private-evidence://{self.planned_call_id}"

    def native_tool_call(self) -> dict[str, object]:
        return {
            "id": self.runtime_call_id,
            "name": self.tool_symbol,
            "args": dict(self.arguments),
            "type": "tool_call",
        }


def response_digest(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def build_session(
    calls: Sequence[EvidenceCall],
    *,
    intervention_target: str | None = None,
    replacement_content: str | None = None,
) -> tuple[LangGraphToolNodeReplaySession, EvidenceResolutionPlan]:
    """Build the frozen plan, private evidence store, and real LangGraph session."""

    planned_calls = tuple(
        ReplayToolCall(
            call_id=call.planned_call_id,
            kind=ReplayToolCallKind.PLANNED,
            scope_id="graph.root",
            tool_symbol=call.tool_symbol,
            tool_schema_version="v1",
            argument_digest=create_tool_argument_digest(
                call.arguments,
                algorithm=ToolArgumentDigestAlgorithm.SHA256_JSON_V1,
            ),
            occurrence=call.occurrence,
            evidence_reference=call.evidence_reference,
            response_digest=response_digest(call.recorded_content),
        )
        for call in calls
    )
    plan = EvidenceResolutionPlan(
        organization_id="tutorial-org",
        project_id="tutorial-project",
        source_execution_id="observed-execution-1",
        replay_manifest_hash=MANIFEST_HASH,
        capability=ExecutionReplayCapability(
            supported_tool_boundaries=("langgraph.tool-node",),
            unsupported_execution_surfaces=(
                "custom-graph-nodes",
                "direct-sdk-calls",
                "provider-hosted-tools",
            ),
            argument_digest_algorithms=(ToolArgumentDigestAlgorithm.SHA256_JSON_V1,),
            max_trace_size=16,
            supports_async_tools=True,
            supports_subgraphs=False,
            supports_retries=True,
            supports_concurrent_execution=True,
            evidence_only=True,
            no_original_tool_fallback=True,
        ),
        planned_calls=planned_calls,
        evidence_bindings=tuple(
            ReplayEvidenceBinding(
                call.call_id,
                call.evidence_reference or "",
                call.response_digest or "",
            )
            for call in planned_calls
        ),
    )
    private_evidence = {
        call.evidence_reference: PrivateToolEvidence(
            content=call.recorded_content,
            response_digest=response_digest(call.recorded_content),
        )
        for call in calls
    }
    intervention = None
    if intervention_target is not None:
        if replacement_content is None:
            raise ValueError("An intervention needs replacement private evidence.")
        if intervention_target not in {call.planned_call_id for call in calls}:
            raise ValueError("The intervention target is not present in the plan.")
        replacement_reference = f"private-evidence://{intervention_target}/replacement"
        private_evidence[replacement_reference] = PrivateToolEvidence(
            content=replacement_content,
            response_digest=response_digest(replacement_content),
        )
        intervention = ReplayInterventionBinding(
            replay_manifest_hash=plan.replay_manifest_hash,
            resolution_plan_digest=plan.plan_digest,
            target_planned_call_id=intervention_target,
            intervention_digest="sha256:" + "f" * 64,
            replacement_evidence_reference=replacement_reference,
            replacement_response_digest=response_digest(replacement_content),
        )

    bindings = tuple(
        LangGraphToolCallBinding(
            runtime_tool_call_id=call.runtime_call_id,
            tool_symbol=call.tool_symbol,
            scope_id="graph.root",
            tool_schema_version="v1",
            occurrence=call.occurrence,
        )
        for call in calls
    )
    return (
        LangGraphToolNodeReplaySession(
            plan,
            bindings=bindings,
            evidence=PrivateToolEvidenceStore(private_evidence),
            intervention=intervention,
        ),
        plan,
    )
