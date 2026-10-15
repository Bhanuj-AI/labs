"""A LangGraph workflow whose protected write always crosses one boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, TypedDict

from governance import EvidenceStatus, ExecutionEvidenceReporter
from langgraph.graph import END, START, StateGraph  # type: ignore[import-not-found]
from operations import OperationIntent, OperationRequest, OperationsRepository
from policy import (
    AuthorizationOutcome,
    OperationAuthorizer,
)


class InterruptedExecution(RuntimeError):
    """A known terminal interruption, never a successful completion."""


class OperationsState(TypedDict, total=False):
    request: OperationRequest
    record_status: str
    proposed_status: str
    outcome: str
    reason_code: str
    decision_reference: str
    tool_call_id: str


@dataclass(frozen=True)
class WorkflowDependencies:
    repository: OperationsRepository
    authorizer: OperationAuthorizer
    reporter: ExecutionEvidenceReporter


@dataclass(frozen=True)
class WorkflowRunResult:
    external_execution_id: str
    governance_execution_id: str | None
    outcome: str
    reason_code: str | None
    evidence_status: EvidenceStatus
    evidence_error: str | None


Node = Callable[[OperationsState], dict[str, object]]


def build_workflow(dependencies: WorkflowDependencies):
    """Build the graph; only the protected node can mutate SQLite."""

    graph = StateGraph(OperationsState)
    graph.add_node(
        "read_operational_record",
        _observe_node(dependencies.reporter, "read_operational_record", _read_record(dependencies)),
    )
    graph.add_node(
        "propose_database_change",
        _observe_node(
            dependencies.reporter,
            "propose_database_change",
            _propose_database_change,
        ),
    )
    graph.add_node(
        "interrupted_execution",
        _observe_node(dependencies.reporter, "interrupted_execution", _interrupt),
    )
    graph.add_node(
        "protected_database_write",
        _observe_node(
            dependencies.reporter,
            "protected_database_write",
            _protected_database_write(dependencies),
        ),
    )

    graph.add_edge(START, "read_operational_record")
    graph.add_conditional_edges(
        "read_operational_record",
        _after_read,
        {
            "finish": END,
            "propose": "propose_database_change",
            "interrupt": "interrupted_execution",
        },
    )
    graph.add_edge("propose_database_change", "protected_database_write")
    graph.add_edge("protected_database_write", END)
    return graph.compile()


def run_workflow(
    request: OperationRequest,
    dependencies: WorkflowDependencies,
) -> WorkflowRunResult:
    """Register optional evidence, retain LangGraph execution ownership, finalize."""

    reporter = dependencies.reporter
    reporter.start(
        {
            "workflow_name": "governed_operations",
            "workflow_version": "1.0.0",
            "model_reference": "none",
            "operation_intent": request.intent.value,
            "langgraph_run_id": request.langgraph_run_id,
            **(
                {"langgraph_thread_id": request.langgraph_thread_id}
                if request.langgraph_thread_id is not None
                else {}
            ),
        }
    )
    # The control plane, not LangGraph, assigns the canonical execution ID.
    # A later protected operation receives it only after registration succeeds.
    invocation_request = replace(
        request,
        governance_execution_id=reporter.execution_id,
    )
    invocation_request = _bind_delegation(invocation_request, dependencies.authorizer)
    try:
        state = build_workflow(dependencies).invoke({"request": invocation_request})
    except InterruptedExecution:
        reporter.error_event("InterruptedExecution", reason_code="EXECUTION_INTERRUPTED")
        reporter.finalize("CANCELLED")
        return _result(
            invocation_request, reporter, "INTERRUPTED", "EXECUTION_INTERRUPTED"
        )
    except Exception as error:
        reporter.error_event(type(error).__name__, reason_code="WORKFLOW_FAILED")
        reporter.finalize("FAILED")
        raise

    outcome = str(state["outcome"])
    reason_code = str(state.get("reason_code", "")) or None
    # A deliberate denied operation is a handled workflow outcome. An
    # unavailable authorizer is a failed protected operation and is never
    # recorded as a successful execution.
    reporter.finalize(
        "FAILED" if outcome == "GOVERNANCE_UNAVAILABLE" else "SUCCEEDED"
    )
    return _result(invocation_request, reporter, outcome, reason_code)


async def run_workflow_async(
    request: OperationRequest,
    dependencies: WorkflowDependencies,
) -> WorkflowRunResult:
    """Run the same invocation contract through LangGraph's async path.

    Evidence delivery stays ordered and bounded through the adapter's existing
    synchronous public REST client. Framework applications may call this from
    their own async lifecycle without changing execution identity or protected
    tool semantics.
    """

    reporter = dependencies.reporter
    reporter.start(
        {
            "workflow_name": "governed_operations",
            "workflow_version": "1.0.0",
            "model_reference": "none",
            "operation_intent": request.intent.value,
            "langgraph_run_id": request.langgraph_run_id,
            **(
                {"langgraph_thread_id": request.langgraph_thread_id}
                if request.langgraph_thread_id is not None
                else {}
            ),
        }
    )
    invocation_request = replace(
        request,
        governance_execution_id=reporter.execution_id,
    )
    invocation_request = _bind_delegation(invocation_request, dependencies.authorizer)
    try:
        state = await build_workflow(dependencies).ainvoke(
            {"request": invocation_request}
        )
    except InterruptedExecution:
        reporter.error_event("InterruptedExecution", reason_code="EXECUTION_INTERRUPTED")
        reporter.finalize("CANCELLED")
        return _result(
            invocation_request, reporter, "INTERRUPTED", "EXECUTION_INTERRUPTED"
        )
    except Exception as error:
        reporter.error_event(type(error).__name__, reason_code="WORKFLOW_FAILED")
        reporter.finalize("FAILED")
        raise

    outcome = str(state["outcome"])
    reason_code = str(state.get("reason_code", "")) or None
    reporter.finalize("FAILED" if outcome == "GOVERNANCE_UNAVAILABLE" else "SUCCEEDED")
    return _result(invocation_request, reporter, outcome, reason_code)


def _result(
    request: OperationRequest,
    reporter: ExecutionEvidenceReporter,
    outcome: str,
    reason_code: str | None,
) -> WorkflowRunResult:
    return WorkflowRunResult(
        external_execution_id=request.external_execution_id,
        governance_execution_id=reporter.execution_id,
        outcome=outcome,
        reason_code=reason_code,
        evidence_status=reporter.status,
        evidence_error=reporter.error,
    )


def _bind_delegation(
    request: OperationRequest,
    authorizer: OperationAuthorizer,
) -> OperationRequest:
    """Permit only an explicit issuer-capable adapter to attach a grant."""
    binder = getattr(authorizer, "bind_delegation", None)
    if not callable(binder):
        return request
    try:
        bound = binder(request)
    except Exception:  # noqa: BLE001 - a failed issuer must fail closed later.
        return request
    return bound if isinstance(bound, OperationRequest) else request


def _read_record(dependencies: WorkflowDependencies) -> Node:
    def node(state: OperationsState) -> dict[str, object]:
        record = dependencies.repository.read(state["request"].record_id)
        result: dict[str, object] = {"record_status": record["status"]}
        if state["request"].intent is OperationIntent.READ:
            result["outcome"] = "READ_COMPLETED"
        return result

    return node


def _propose_database_change(state: OperationsState) -> dict[str, object]:
    return {"proposed_status": state["request"].requested_status}


def _interrupt(state: OperationsState) -> dict[str, object]:
    del state
    raise InterruptedExecution("The developer interrupted the run after the read.")


def _protected_database_write(dependencies: WorkflowDependencies) -> Node:
    def node(state: OperationsState) -> dict[str, object]:
        request = state["request"]
        resource_reference = f"operational-record:{request.record_id}"
        tool_call_id = f"{request.external_execution_id}:tool:operations.update_status"
        try:
            decision = dependencies.authorizer.authorize(request)
        except Exception:  # noqa: BLE001 - protected-tool adapters must fail closed.
            # A faulty adapter has the same fail-closed outcome as an absent
            # authoritative decision service.
            from policy import AuthorizationDecision

            decision = AuthorizationDecision(
                outcome=AuthorizationOutcome.UNAVAILABLE,
                reason_code="AUTHORIZATION_ADAPTER_FAILED",
                source="authorization-adapter",
            )
        # AIGP persists an authoritative decision as
        # RUNTIME_ACTION_AUTHORIZATION. Local fixtures remain explicitly
        # labelled educational GOVERNANCE_DECISION evidence.
        decision_event_id = (
            dependencies.reporter.governance_decision(decision)
            if decision.source == "simulated-lab-fixture"
            else None
        )

        if not decision.allowed:
            if decision.outcome is AuthorizationOutcome.UNAVAILABLE:
                dependencies.reporter.error_event(
                    "AuthorizationUnavailable",
                    reason_code=decision.reason_code,
                    causation_id=decision_event_id,
                )
            outcome = (
                "WRITE_DENIED"
                if decision.outcome is AuthorizationOutcome.DENY
                else "GOVERNANCE_UNAVAILABLE"
            )
            return {
                "outcome": outcome,
                "reason_code": decision.reason_code,
                "tool_call_id": tool_call_id,
            }

        change = dependencies.repository.update_status(
            request.record_id, request.requested_status
        )
        dependencies.reporter.tool_completed(
            tool_call_id,
            outcome="SUCCEEDED",
            resource_reference=resource_reference,
            causation_id=decision.decision_reference or decision_event_id,
        )
        return {
            "outcome": "WRITE_COMPLETED",
            "reason_code": decision.reason_code,
            "decision_reference": decision.decision_reference or "",
            "tool_call_id": tool_call_id,
            "record_status": change.new_status,
        }

    return node


def _after_read(state: OperationsState) -> Literal["finish", "propose", "interrupt"]:
    request = state["request"]
    if request.interrupt_after_read:
        return "interrupt"
    return "finish" if request.intent is OperationIntent.READ else "propose"


def _observe_node(
    reporter: ExecutionEvidenceReporter, node_name: str, node: Node
) -> Node:
    def observed(state: OperationsState) -> dict[str, object]:
        reporter.workflow_step(node_name, "STARTED")
        try:
            result = node(state)
        except Exception as error:
            reporter.workflow_step(
                node_name,
                "FAILED",
                attributes={"error_class": type(error).__name__},
            )
            raise
        reporter.workflow_step(
            node_name,
            "COMPLETED",
            attributes={"result_fields": sorted(result)},
        )
        return result

    return observed
