"""Explicitly opted-in integration checks against a local OSS API."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from governance import AgentsRuntimeClient, EvidenceStatus, ExecutionEvidenceReporter, GovernanceSettings
from operations import OperationIntent, OperationRequest, OperationsRepository
from policy import AuthorizationOutcome, SimulatedOperationAuthorizer
from workflow import WorkflowDependencies, run_workflow


@pytest.mark.integration
def test_running_oss_api_persists_correlated_execution_events_and_duplicates(
    tmp_path: Path,
) -> None:
    settings = _integration_settings()
    external_execution_id = f"langgraph-oss-integration-{uuid4()}"
    reporter = ExecutionEvidenceReporter(
        AgentsRuntimeClient(settings),
        external_execution_id=external_execution_id,
        correlation_id=external_execution_id,
    )
    repository = OperationsRepository(tmp_path / "operations.db")
    repository.initialize()
    request = OperationRequest(
        external_execution_id=external_execution_id,
        correlation_id=external_execution_id,
        actor_id=settings.actor_id or "local-admin",
        organization_id=settings.organization_id,
        project_id=settings.project_id,
        intent=OperationIntent.WRITE,
    )

    result = run_workflow(
        request,
        WorkflowDependencies(
            repository=repository,
            authorizer=SimulatedOperationAuthorizer(AuthorizationOutcome.ALLOW),
            reporter=reporter,
        ),
    )

    assert result.outcome == "WRITE_COMPLETED"
    assert repository.read("OPS-001")["status"] == "MAINTENANCE"
    if result.evidence_status is not EvidenceStatus.COMPLETED:
        pytest.fail(
            "The configured BHANUJ OSS API did not accept integration evidence: "
            f"{result.evidence_error}. Start it with "
            "AI_GOVERNANCE_AUTH_MODE=development or provide a valid "
            "BHANUJ_LAB_INTEGRATION_ACCESS_TOKEN."
        )
    assert result.governance_execution_id is not None

    detail = _get_execution(settings, result.governance_execution_id)
    assert detail["execution"]["external_execution_id"] == external_execution_id
    assert detail["execution"]["status"] == "SUCCEEDED"
    event_types = [event["event_type"] for event in detail["events"]]
    assert "EXECUTION_STARTED" in event_types
    assert "WORKFLOW_STEP" in event_types
    assert "TOOL_CALL" in event_types
    assert "GOVERNANCE_DECISION" in event_types
    assert "EXECUTION_COMPLETED" in event_types
    tool_events = [event for event in detail["events"] if event["event_type"] == "TOOL_CALL"]
    assert len(tool_events) == 1
    assert tool_events[0]["attributes"]["outcome"] == "SUCCEEDED"
    assert all(
        event["correlation_id"] == external_execution_id
        for event in detail["events"]
    )

    duplicate_external_execution_id = f"langgraph-oss-duplicate-{uuid4()}"
    duplicate_reporter = ExecutionEvidenceReporter(
        AgentsRuntimeClient(settings),
        external_execution_id=duplicate_external_execution_id,
        correlation_id=duplicate_external_execution_id,
    )
    duplicate_reporter.start({"workflow_name": "duplicate_probe", "workflow_version": "1"})
    first_event_id = duplicate_reporter.workflow_step("probe", "STARTED")
    second_event_id = duplicate_reporter.workflow_step("probe", "STARTED")
    duplicate_reporter.workflow_step("probe", "COMPLETED")
    duplicate_reporter.finalize("SUCCEEDED")

    assert duplicate_reporter.status is EvidenceStatus.COMPLETED
    assert first_event_id == second_event_id
    assert duplicate_reporter.execution_id is not None
    duplicate_detail = _get_execution(settings, duplicate_reporter.execution_id)
    probe_started = [
        event
        for event in duplicate_detail["events"]
        if event["event_type"] == "WORKFLOW_STEP"
        and event["step_name"] == "probe"
        and event["lifecycle"] == "STARTED"
    ]
    assert len(probe_started) == 1


def _integration_settings() -> GovernanceSettings:
    if os.getenv("BHANUJ_LAB_INTEGRATION_ENABLED", "").lower() not in {
        "1",
        "true",
        "yes",
    }:
        pytest.skip(
            "Set BHANUJ_LAB_INTEGRATION_ENABLED=true to opt in to local OSS API checks."
        )
    base_url = os.getenv("BHANUJ_LAB_INTEGRATION_BASE_URL")
    if not base_url:
        pytest.skip(
            "Set BHANUJ_LAB_INTEGRATION_BASE_URL after starting the local BHANUJ OSS API."
        )
    return GovernanceSettings(
        base_url=base_url.rstrip("/"),
        organization_id=os.getenv(
            "BHANUJ_LAB_INTEGRATION_ORGANIZATION_ID", "org_default"
        ),
        project_id=os.getenv("BHANUJ_LAB_INTEGRATION_PROJECT_ID", "project_default"),
        actor_id=os.getenv("BHANUJ_LAB_INTEGRATION_ACTOR_ID", "local-admin"),
        access_token=os.getenv("BHANUJ_LAB_INTEGRATION_ACCESS_TOKEN"),
    )


def _get_execution(settings: GovernanceSettings, execution_id: str) -> dict[str, object]:
    headers = {
        "X-AI-Governance-Organization-Id": settings.organization_id,
        "X-AI-Governance-Project-Id": settings.project_id,
    }
    if settings.actor_id:
        headers["X-AI-Governance-Actor-Id"] = settings.actor_id
    if settings.access_token:
        headers["Authorization"] = f"Bearer {settings.access_token}"
    request = Request(
        f"{settings.base_url}/api/v1/agent-executions/{execution_id}",
        headers=headers,
        method="GET",
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - configured local URL
        return json.loads(response.read().decode("utf-8"))
