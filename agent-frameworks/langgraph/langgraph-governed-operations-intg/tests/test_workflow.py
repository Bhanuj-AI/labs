from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from governance import (
    AgentsRuntimeClient,
    EvidenceStatus,
    ExecutionEvidenceReporter,
    FaultInjectingTransport,
    GovernanceDeliveryError,
    GovernanceSettings,
)
from operations import OperationIntent, OperationRequest, OperationsRepository
from policy import (
    AuthorizationOutcome,
    FixtureOperationAuthorizer,
    OperationAuthorizer,
    SimulatedOperationAuthorizer,
    UnsupportedOperationAuthorizer,
)
from workflow import WorkflowDependencies, run_workflow


class InMemoryAgentsRuntimeTransport:
    """A narrow simulation of the three verified public REST calls."""

    def __init__(self, *, retry_failures: int = 0) -> None:
        self.requests: list[tuple[str, Mapping[str, str], dict[str, Any]]] = []
        self.events_by_key: dict[str, dict[str, Any]] = {}
        self.runtime_tool_call_ids: set[str] = set()
        self.retry_failures = retry_failures

    def __call__(
        self, url: str, headers: Mapping[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.requests.append((url, headers, payload))
        if self.retry_failures:
            self.retry_failures -= 1
            raise GovernanceDeliveryError("temporary outage", retryable=True)
        if url.endswith("/agent-executions"):
            return {"execution": {"execution_id": "governance-execution-001"}}
        if url.endswith("/events"):
            key = str(payload["idempotency_key"])
            existing = self.events_by_key.get(key)
            if existing is not None:
                assert existing == payload
            else:
                if payload["event_type"] == "TOOL_CALL":
                    runtime_tool_call_id = payload["tool_call_context"][
                        "runtime_tool_call_id"
                    ]
                    assert runtime_tool_call_id not in self.runtime_tool_call_ids
                    self.runtime_tool_call_ids.add(runtime_tool_call_id)
                self.events_by_key[key] = payload
            return {"event": {"event_id": f"event-{len(self.events_by_key):03d}"}}
        assert url.endswith("/complete")
        return {"execution": {"execution_id": "governance-execution-001"}}


def _request(intent: OperationIntent, **kwargs: object) -> OperationRequest:
    return OperationRequest(
        external_execution_id="langgraph-operations-test-001",
        correlation_id="corr-operations-test-001",
        actor_id="operations-assistant",
        organization_id="org_default",
        project_id="project_default",
        intent=intent,
        **kwargs,
    )


def _dependencies(
    tmp_path: Path,
    authorizer: OperationAuthorizer,
    reporter: ExecutionEvidenceReporter,
) -> tuple[OperationsRepository, WorkflowDependencies]:
    repository = OperationsRepository(tmp_path / "operations.db")
    repository.initialize()
    return repository, WorkflowDependencies(
        repository=repository, authorizer=authorizer, reporter=reporter
    )


def _reporter(transport: InMemoryAgentsRuntimeTransport) -> ExecutionEvidenceReporter:
    client = AgentsRuntimeClient(
        GovernanceSettings("http://governance.test", "org_default", "project_default"),
        transport=transport,
        sleep=lambda _seconds: None,
    )
    return ExecutionEvidenceReporter(
        client,
        external_execution_id="langgraph-operations-test-001",
        correlation_id="corr-operations-test-001",
    )


def test_read_only_operation_runs_without_bhanuj_or_a_policy_decision(tmp_path: Path) -> None:
    reporter = ExecutionEvidenceReporter(
        None,
        external_execution_id="langgraph-operations-test-001",
        correlation_id="corr-operations-test-001",
    )
    repository, dependencies = _dependencies(
        tmp_path, UnsupportedOperationAuthorizer(), reporter
    )

    result = run_workflow(_request(OperationIntent.READ), dependencies)

    assert result.outcome == "READ_COMPLETED"
    assert repository.read("OPS-001")["status"] == "PENDING"
    assert result.evidence_status is EvidenceStatus.DISABLED


def test_read_only_operation_records_accepted_evidence_when_enabled(tmp_path: Path) -> None:
    transport = InMemoryAgentsRuntimeTransport()
    repository, dependencies = _dependencies(
        tmp_path, UnsupportedOperationAuthorizer(), _reporter(transport)
    )

    result = run_workflow(_request(OperationIntent.READ), dependencies)

    assert result.outcome == "READ_COMPLETED"
    assert result.evidence_status is EvidenceStatus.COMPLETED
    assert repository.read("OPS-001")["status"] == "PENDING"
    events = list(transport.events_by_key.values())
    assert [event["event_type"] for event in events] == [
        "WORKFLOW_STEP",
        "WORKFLOW_STEP",
    ]
    assert events[0]["step_id"] == events[1]["step_id"]
    complete_payloads = [
        payload for url, _headers, payload in transport.requests if url.endswith("/complete")
    ]
    assert complete_payloads == [{"status": "SUCCEEDED"}]


def test_fixture_authorized_write_mutates_sqlite_only_after_allow(tmp_path: Path) -> None:
    transport = InMemoryAgentsRuntimeTransport()
    repository, dependencies = _dependencies(
        tmp_path,
        FixtureOperationAuthorizer(AuthorizationOutcome.ALLOW),
        _reporter(transport),
    )

    result = run_workflow(_request(OperationIntent.WRITE), dependencies)

    assert result.outcome == "WRITE_COMPLETED"
    assert repository.read("OPS-001")["status"] == "MAINTENANCE"
    assert result.governance_execution_id == "governance-execution-001"
    assert result.evidence_status is EvidenceStatus.COMPLETED
    start_headers = transport.requests[0][1]
    assert start_headers["X-AI-Governance-Organization-Id"] == "org_default"
    assert start_headers["X-AI-Governance-Project-Id"] == "project_default"
    assert start_headers["X-Correlation-Id"] == "corr-operations-test-001"
    decision_events = [
        payload
        for payload in transport.events_by_key.values()
        if payload["event_type"] == "GOVERNANCE_DECISION"
    ]
    assert decision_events[0]["attributes"]["decision_source"] == "simulated-lab-fixture"
    tool_events = [
        payload
        for payload in transport.events_by_key.values()
        if payload["event_type"] == "TOOL_CALL"
    ]
    assert len(tool_events) == 1
    assert tool_events[0]["attributes"]["outcome"] == "SUCCEEDED"


def test_denied_write_cannot_mutate_sqlite(tmp_path: Path) -> None:
    transport = InMemoryAgentsRuntimeTransport()
    repository, dependencies = _dependencies(
        tmp_path,
        FixtureOperationAuthorizer(AuthorizationOutcome.DENY),
        _reporter(transport),
    )

    result = run_workflow(_request(OperationIntent.WRITE), dependencies)

    assert result.outcome == "WRITE_DENIED"
    assert repository.read("OPS-001")["status"] == "PENDING"
    assert not [
        payload
        for payload in transport.events_by_key.values()
        if payload["event_type"] == "TOOL_CALL"
    ]


def test_absent_public_authorizer_fails_closed_before_mutation(tmp_path: Path) -> None:
    repository, dependencies = _dependencies(
        tmp_path,
        UnsupportedOperationAuthorizer(),
        ExecutionEvidenceReporter(
            None,
            external_execution_id="langgraph-operations-test-001",
            correlation_id="corr-operations-test-001",
        ),
    )

    result = run_workflow(_request(OperationIntent.WRITE), dependencies)

    assert result.outcome == "GOVERNANCE_UNAVAILABLE"
    assert result.reason_code == "PUBLIC_OPERATION_AUTHORIZATION_UNAVAILABLE"
    assert repository.read("OPS-001")["status"] == "PENDING"


def test_unavailable_simulated_authorizer_emits_error_evidence_and_fails_closed(
    tmp_path: Path,
) -> None:
    transport = InMemoryAgentsRuntimeTransport()
    repository, dependencies = _dependencies(
        tmp_path,
        SimulatedOperationAuthorizer(AuthorizationOutcome.UNAVAILABLE),
        _reporter(transport),
    )

    result = run_workflow(_request(OperationIntent.WRITE), dependencies)

    assert result.outcome == "GOVERNANCE_UNAVAILABLE"
    assert result.reason_code == "SIMULATED_AUTHORIZER_OUTAGE"
    assert repository.read("OPS-001")["status"] == "PENDING"
    errors = [
        payload
        for payload in transport.events_by_key.values()
        if payload["event_type"] == "ERROR"
    ]
    assert errors[0]["attributes"]["error_class"] == "AuthorizationUnavailable"
    complete_payloads = [
        payload for url, _headers, payload in transport.requests if url.endswith("/complete")
    ]
    assert complete_payloads == [{"status": "FAILED"}]


def test_registration_failure_at_workflow_level_leaves_read_only_result_intact(
    tmp_path: Path,
) -> None:
    transport = FaultInjectingTransport(
        InMemoryAgentsRuntimeTransport(), fail_stage="registration"
    )
    client = AgentsRuntimeClient(
        GovernanceSettings("http://governance.test", "org_default", "project_default"),
        transport=transport,
        sleep=lambda _seconds: None,
    )
    reporter = ExecutionEvidenceReporter(
        client,
        external_execution_id="langgraph-operations-test-001",
        correlation_id="corr-operations-test-001",
    )
    repository, dependencies = _dependencies(
        tmp_path, UnsupportedOperationAuthorizer(), reporter
    )

    result = run_workflow(_request(OperationIntent.READ), dependencies)

    assert result.outcome == "READ_COMPLETED"
    assert result.evidence_status is EvidenceStatus.DEGRADED
    assert result.evidence_error == "Simulated execution registration failure."
    assert repository.read("OPS-001")["status"] == "PENDING"


def test_evidence_delivery_failure_at_workflow_level_stops_reporting(
    tmp_path: Path,
) -> None:
    underlying = InMemoryAgentsRuntimeTransport()
    client = AgentsRuntimeClient(
        GovernanceSettings("http://governance.test", "org_default", "project_default"),
        transport=FaultInjectingTransport(underlying, fail_stage="evidence"),
        sleep=lambda _seconds: None,
    )
    reporter = ExecutionEvidenceReporter(
        client,
        external_execution_id="langgraph-operations-test-001",
        correlation_id="corr-operations-test-001",
    )
    repository, dependencies = _dependencies(
        tmp_path, UnsupportedOperationAuthorizer(), reporter
    )

    result = run_workflow(_request(OperationIntent.READ), dependencies)

    assert result.outcome == "READ_COMPLETED"
    assert result.evidence_status is EvidenceStatus.DEGRADED
    assert result.evidence_error == "Simulated runtime evidence outage."
    assert repository.read("OPS-001")["status"] == "PENDING"
    assert not underlying.events_by_key
    assert not any(url.endswith("/complete") for url, _headers, _body in underlying.requests)


def test_duplicate_evidence_key_is_one_logical_event_and_retries_are_bounded() -> None:
    transport = InMemoryAgentsRuntimeTransport(retry_failures=1)
    reporter = _reporter(transport)

    reporter.start({"workflow_name": "governed_operations", "workflow_version": "1"})
    first = reporter.workflow_step("read_operational_record", "STARTED")
    second = reporter.workflow_step("read_operational_record", "STARTED")

    assert first == second == "event-001"
    assert len(transport.events_by_key) == 1
    # Start was retried once, and both duplicate event deliveries carried the
    # same idempotency key. The authoritative service performs deduplication.
    assert len(transport.requests) == 4


def test_transient_delivery_stops_after_three_attempts() -> None:
    transport = InMemoryAgentsRuntimeTransport(retry_failures=5)
    client = AgentsRuntimeClient(
        GovernanceSettings("http://governance.test", "org_default", "project_default"),
        transport=transport,
        sleep=lambda _seconds: None,
    )

    try:
        client.start_execution(
            external_execution_id="langgraph-operations-test-001",
            correlation_id="corr-operations-test-001",
            metadata={"workflow_name": "governed_operations"},
        )
    except GovernanceDeliveryError as error:
        assert error.retryable is True
    else:
        raise AssertionError("A persistent delivery failure must not be treated as success.")

    assert len(transport.requests) == 3


def test_interrupted_execution_is_cancelled_and_never_mutates_sqlite(tmp_path: Path) -> None:
    transport = InMemoryAgentsRuntimeTransport()
    repository, dependencies = _dependencies(
        tmp_path,
        FixtureOperationAuthorizer(AuthorizationOutcome.ALLOW),
        _reporter(transport),
    )

    result = run_workflow(
        _request(OperationIntent.WRITE, interrupt_after_read=True), dependencies
    )

    assert result.outcome == "INTERRUPTED"
    assert repository.read("OPS-001")["status"] == "PENDING"
    complete_payloads = [
        payload for url, _headers, payload in transport.requests if url.endswith("/complete")
    ]
    assert complete_payloads == [{"status": "CANCELLED"}]


def test_failed_execution_finalizes_as_failed_and_records_error_evidence(
    tmp_path: Path,
) -> None:
    transport = InMemoryAgentsRuntimeTransport()
    repository, dependencies = _dependencies(
        tmp_path,
        UnsupportedOperationAuthorizer(),
        _reporter(transport),
    )

    with pytest.raises(KeyError):
        run_workflow(
            _request(OperationIntent.READ, record_id="OPS-DOES-NOT-EXIST"),
            dependencies,
        )

    assert repository.read("OPS-001")["status"] == "PENDING"
    complete_payloads = [
        payload for url, _headers, payload in transport.requests if url.endswith("/complete")
    ]
    assert complete_payloads == [{"status": "FAILED"}]
    errors = [
        payload
        for payload in transport.events_by_key.values()
        if payload["event_type"] == "ERROR"
    ]
    assert errors[0]["attributes"]["error_class"] == "KeyError"
