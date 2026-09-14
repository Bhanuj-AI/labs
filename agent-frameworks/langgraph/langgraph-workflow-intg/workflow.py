"""A small deterministic insurance-claim workflow built with LangGraph."""

from collections.abc import Callable
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph # type: ignore


class ClaimState(TypedDict, total=False):
    """The data carried through each stage of the claim workflow."""

    claim: dict[str, object]
    claim_id: str
    policy_active: bool
    damage_verified: bool
    decision: Literal["APPROVED", "REJECTED"]


def load_claim(state: ClaimState) -> ClaimState:
    """Copy the fixture claim into explicit workflow state fields."""
    claim = state["claim"]
    return {"claim_id": str(claim["claim_id"])}


def check_policy(state: ClaimState) -> ClaimState:
    """Determine whether the policy is active."""
    return {"policy_active": bool(state["claim"]["policy_active"])}


def evaluate_evidence(state: ClaimState) -> ClaimState:
    """Determine whether the reported damage has been verified."""
    return {"damage_verified": bool(state["claim"]["damage_verified"])}


def make_decision(state: ClaimState) -> ClaimState:
    """Approve only an active-policy claim with verified damage."""
    decision: Literal["APPROVED", "REJECTED"] = (
        "APPROVED"
        if state["policy_active"] and state["damage_verified"]
        else "REJECTED"
    )
    return {"decision": decision}


Node = Callable[[ClaimState], ClaimState]
NodeWrapper = Callable[[str, Node], Node]


def build_workflow(node_wrapper: NodeWrapper | None = None):
    """Compile and return the fixed claim-processing graph."""
    graph = StateGraph(ClaimState)
    wrap = node_wrapper or (lambda _name, node: node)
    graph.add_node("load_claim", wrap("load_claim", load_claim))
    graph.add_node("check_policy", wrap("check_policy", check_policy))
    graph.add_node("evaluate_evidence", wrap("evaluate_evidence", evaluate_evidence))
    graph.add_node("make_decision", wrap("make_decision", make_decision))

    graph.add_edge(START, "load_claim")
    graph.add_edge("load_claim", "check_policy")
    graph.add_edge("check_policy", "evaluate_evidence")
    graph.add_edge("evaluate_evidence", "make_decision")
    graph.add_edge("make_decision", END)

    return graph.compile()
