"""Run deterministic LangGraph operations scenarios from the command line."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from uuid import uuid4

from governance import (
    AgentsRuntimeClient,
    ExecutionEvidenceReporter,
    FaultInjectingTransport,
    GovernanceSettings,
)
from operations import OperationIntent, OperationRequest, OperationsRepository
from policy import (
    AIGPRuntimeActionAuthorizer,
    AuthorizationOutcome,
    SimulatedOperationAuthorizer,
    UnsupportedOperationAuthorizer,
)
from workflow import WorkflowDependencies, run_workflow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenario",
        choices=(
            "readonly",
            "allowed",
            "denied",
            "outage",
            "aigp",
            "registration-failure",
            "evidence-failure",
        ),
        nargs="?",
        default="readonly",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(__file__).parent / "operations.db",
        help="SQLite database path (default: ./operations.db)",
    )
    parser.add_argument(
        "--interrupt-after-read",
        action="store_true",
        help="End the run as CANCELLED after the read node, without a write.",
    )
    parser.add_argument(
        "--langgraph-run-id",
        help=(
            "Actual LangGraph invocation/run ID. The standalone lab generates "
            "one fresh ID at the invocation boundary when omitted."
        ),
    )
    parser.add_argument(
        "--langgraph-thread-id",
        help="Optional LangGraph thread/checkpoint ID; never an AIGP execution ID.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    execution_id = args.langgraph_run_id or f"langgraph-operations-{uuid4()}"
    intent = (
        OperationIntent.WRITE
        if args.scenario in {"allowed", "denied", "outage", "aigp"}
        else OperationIntent.READ
    )
    request = OperationRequest(
        external_execution_id=execution_id,
        correlation_id=execution_id,
        actor_id=os.getenv(
            "AI_GOVERNANCE_RUNTIME_ACTOR_ID",
            os.getenv("AI_GOVERNANCE_ACTOR_ID", "operations-assistant"),
        ),
        organization_id=os.getenv("AI_GOVERNANCE_ORGANIZATION_ID", "org_default"),
        project_id=os.getenv("AI_GOVERNANCE_PROJECT_ID", "project_default"),
        intent=intent,
        interrupt_after_read=args.interrupt_after_read,
        langgraph_thread_id=args.langgraph_thread_id,
    )
    repository = OperationsRepository(args.database)
    repository.initialize()
    reporter = _reporter_for(args.scenario, request)
    authorizer = _authorizer_for(args.scenario)
    result = run_workflow(
        request,
        WorkflowDependencies(
            repository=repository,
            authorizer=authorizer,
            reporter=reporter,
        ),
    )

    print(f"External execution ID: {result.external_execution_id}")
    print(f"Outcome: {result.outcome}")
    print(f"Reason: {result.reason_code or 'n/a'}")
    print(f"SQLite record status: {repository.read(request.record_id)['status']}")
    print(f"AIGP execution ID: {result.governance_execution_id or 'not delivered'}")
    print(f"AIGP evidence status: {result.evidence_status.value}")
    if result.evidence_error:
        print(f"AIGP evidence error: {result.evidence_error}")
    if args.scenario in {"allowed", "denied", "outage"}:
        print("Decision source: simulated lab fixture (not a BHANUJ authorizer)")


def _authorizer_for(scenario: str):
    if scenario == "aigp":
        settings = GovernanceSettings.from_environment()
        if settings is None or settings.runtime_actor_id is None:
            return UnsupportedOperationAuthorizer()
        return AIGPRuntimeActionAuthorizer(
            base_url=settings.base_url,
            organization_id=settings.organization_id,
            project_id=settings.project_id,
            expected_actor_id=settings.runtime_actor_id,
            access_token=settings.access_token,
            development_actor_id=settings.actor_id,
        )
    if scenario == "allowed":
        return SimulatedOperationAuthorizer(AuthorizationOutcome.ALLOW)
    if scenario == "denied":
        return SimulatedOperationAuthorizer(AuthorizationOutcome.DENY)
    if scenario == "outage":
        return SimulatedOperationAuthorizer(AuthorizationOutcome.UNAVAILABLE)
    return UnsupportedOperationAuthorizer()


def _reporter_for(
    scenario: str, request: OperationRequest
) -> ExecutionEvidenceReporter:
    transport = None
    if scenario == "registration-failure":
        transport = FaultInjectingTransport(
            AgentsRuntimeClient._post_json, fail_stage="registration"
        )
    elif scenario == "evidence-failure":
        transport = FaultInjectingTransport(
            AgentsRuntimeClient._post_json, fail_stage="evidence"
        )
    return ExecutionEvidenceReporter.from_environment(
        external_execution_id=request.external_execution_id,
        correlation_id=request.correlation_id,
        transport=transport,
    )


if __name__ == "__main__":
    main()
