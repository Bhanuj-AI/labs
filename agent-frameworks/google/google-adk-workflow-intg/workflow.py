"""A deterministic, local Google ADK insurance-claim workflow."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import START, FunctionNode, Workflow

from governed_workflow import WorkflowStepEvidence, governed_adk_node


STAGE_NAMES = (
    "validate_claim",
    "load_policy",
    "check_coverage",
    "check_policy",
    "calculate_payout",
    "record_decision",
    "notify_customer",
    "complete_claim",
)


def _claim(ctx: Any) -> dict[str, object]:
    claim = ctx.state["claim"]
    if not isinstance(claim, dict):
        raise ValueError("Claim state must be an object.")
    return claim


def validate_claim(ctx: Any) -> dict[str, object]:
    claim = _claim(ctx)
    required = {"claim_id", "customer_id", "claim_amount", "policy_active", "damage_verified"}
    if not required.issubset(claim):
        raise ValueError("Claim fixture is missing required fields.")
    ctx.state["claim_id"] = str(claim["claim_id"])
    return {"claim_valid": True}


def load_policy(ctx: Any) -> dict[str, object]:
    ctx.state["policy_loaded"] = True
    return {"policy_loaded": True}


def check_coverage(ctx: Any) -> dict[str, object]:
    ctx.state["coverage_available"] = True
    return {"coverage_available": True}


def check_policy(ctx: Any) -> dict[str, object]:
    active = bool(_claim(ctx)["policy_active"])
    ctx.state["policy_active"] = active
    return {"policy_active": active}


def calculate_payout(ctx: Any) -> dict[str, object]:
    amount = int(_claim(ctx)["claim_amount"])
    ctx.state["approved_amount"] = amount
    return {"approved_amount": amount}


def record_decision(ctx: Any) -> dict[str, object]:
    claim = _claim(ctx)
    approved = bool(ctx.state["policy_active"]) and bool(claim["damage_verified"])
    ctx.state["damage_verified"] = bool(claim["damage_verified"])
    ctx.state["decision"] = "APPROVED" if approved else "REJECTED"
    return {"decision": ctx.state["decision"]}


def notify_customer(ctx: Any) -> dict[str, object]:
    ctx.state["customer_notified"] = True
    return {"customer_notified": True}


def complete_claim(ctx: Any) -> dict[str, object]:
    ctx.state["claim_completed"] = True
    return {"claim_completed": True}


_STAGES = (
    validate_claim,
    load_policy,
    check_coverage,
    check_policy,
    calculate_payout,
    record_decision,
    notify_customer,
    complete_claim,
)


def build_workflow(evidence: WorkflowStepEvidence) -> Workflow:
    """Build an ADK graph whose function nodes are observed independently."""
    nodes = [
        FunctionNode(
            func=governed_adk_node(evidence, step_name=stage.__name__)(stage),
            name=stage.__name__,
        )
        for stage in _STAGES
    ]
    edges: list[object] = [(START, nodes[0])]
    edges.extend((current, following) for current, following in zip(nodes, nodes[1:]))
    return Workflow(name="insurance_claim_workflow", edges=edges)


async def run_claim(
    claim: Mapping[str, object],
    evidence: WorkflowStepEvidence,
    *,
    invocation_id: str,
) -> dict[str, object]:
    """Run one ADK invocation using only in-memory session state."""
    sessions = InMemorySessionService()
    await sessions.create_session(
        app_name="insurance_claim_lab",
        user_id="local_developer",
        session_id=invocation_id,
        state={"claim": dict(claim)},
    )
    runner = Runner(
        app_name="insurance_claim_lab",
        node=build_workflow(evidence),
        session_service=sessions,
    )
    async for _event in runner.run_async(
        user_id="local_developer",
        session_id=invocation_id,
        invocation_id=invocation_id,
    ):
        pass
    session = await sessions.get_session(
        app_name="insurance_claim_lab",
        user_id="local_developer",
        session_id=invocation_id,
    )
    assert session is not None
    return dict(session.state)


def run_claim_sync(
    claim: Mapping[str, object], evidence: WorkflowStepEvidence, *, invocation_id: str
) -> dict[str, object]:
    """Synchronous CLI-friendly entry point around ADK's async runner."""
    return asyncio.run(run_claim(claim, evidence, invocation_id=invocation_id))
