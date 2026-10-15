from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

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
    AIGPRuntimeActionAuthorizer,
    AuthorizationOutcome,
    FixtureOperationAuthorizer,
    OperationAuthorizer,
    SimulatedOperationAuthorizer,
    UnsupportedOperationAuthorizer,
)
from workflow import WorkflowDependencies, run_workflow, run_workflow_async


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


class InvocationIdentityTransport(InMemoryAgentsRuntimeTransport):
    """Make the simulated public API assign one canonical ID per run ID."""

    def __init__(self) -> None:
        super().__init__()
        self._execution_ids: dict[str, str] = {}

    def __call__(
        self, url: str, headers: Mapping[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
        if url.endswith("/agent-executions"):
            self.requests.append((url, headers, payload))
            run_id = str(payload["external_execution_id"])
            execution_id = self._execution_ids.setdefault(
                run_id, f"governance-{len(self._execution_ids) + 1}"
            )
            return {"execution": {"execution_id": execution_id}}
        return super().__call__(url, headers, payload)


def _runtime_contracts(
    decision: Any,
    *,
    reject_allow: bool = False,
) -> tuple[SimpleNamespace, type[Any]]:
    """Use contract-shaped fakes without importing the OSS SDK in unit tests."""

    class Resource:
        def __init__(self, resource_type: str, resource_reference: str) -> None:
            self.resource_type = resource_type
            self.resource_reference = resource_reference

    class Request:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class Client:
        instances: ClassVar[list[Client]] = []

        def __init__(self, *args: object, **kwargs: object) -> None:
            self.args = args
            self.kwargs = kwargs
            self.request: Request | None = None
            self.__class__.instances.append(self)

        def authorize(self, request: Request) -> Any:
            self.request = request
            return decision

    class Enforcer:
        def __init__(self, client: Client, *, actor_id: str) -> None:
            self.client = client
            self.actor_id = actor_id

        def require_allow(
            self,
            request: Request,
            *,
            actual_tool_arguments_digest: str,
        ) -> Any:
            self.actual_tool_arguments_digest = actual_tool_arguments_digest
            resolved = self.client.authorize(request)
            if reject_allow or resolved.outcome.value != "ALLOW":
                raise RuntimeError(resolved.reason_code)
            return resolved

    return (
        SimpleNamespace(
            client_type=Client,
            enforcer_type=Enforcer,
            request_type=Request,
            resource_type=Resource,
            tool_arguments_digest=lambda values: f"digest:{values['record_id']}:{values['requested_status']}",
            blocked_exception_type=RuntimeError,
        ),
        Client,
    )


def _aigp_decision(outcome: str, decision_id: str = "authorization-001") -> Any:
    return SimpleNamespace(
        outcome=SimpleNamespace(value=outcome),
        reason_code=("ALLOW_POLICY_MATCHED" if outcome == "ALLOW" else "DENY_POLICY_MATCHED"),
        decision_id=decision_id,
    )


def _aigp_authorizer(
    monkeypatch: pytest.MonkeyPatch,
    decision: Any,
    *,
    reject_allow: bool = False,
) -> tuple[AIGPRuntimeActionAuthorizer, type[Any]]:
    contracts, client_type = _runtime_contracts(decision, reject_allow=reject_allow)
    monkeypatch.setattr("policy._runtime_authorization_contracts", lambda: contracts)
    return (
        AIGPRuntimeActionAuthorizer(
            base_url="http://governance.test",
            organization_id="org_default",
            project_id="project_default",
            expected_actor_id="runtime-service",
            development_actor_id="runtime-service",
        ),
        client_type,
    )


def _request(intent: OperationIntent, **kwargs: object) -> OperationRequest:
    values: dict[str, object] = {
        "external_execution_id": "langgraph-operations-test-001",
        "correlation_id": "corr-operations-test-001",
        "actor_id": "operations-assistant",
        "organization_id": "org_default",
        "project_id": "project_default",
        "intent": intent,
        "governance_delegation_id": "delegation-001",
        "governance_delegation_version": "1",
        "governance_delegation_digest": "sha256:" + "a" * 64,
    }
    values.update(kwargs)
    return OperationRequest(**values)  # type: ignore[arg-type]


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
    assert result.reason_code == "AIGP_RUNTIME_AUTHORIZATION_NOT_CONFIGURED"
    assert repository.read("OPS-001")["status"] == "PENDING"


def test_authoritative_authorizer_binds_canonical_execution_and_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer, client_type = _aigp_authorizer(monkeypatch, _aigp_decision("ALLOW"))
    request = _request(
        OperationIntent.WRITE,
        governance_execution_id="aigp-execution-001",
        langgraph_thread_id="thread-shared",
    )

    decision = authorizer.authorize(request)

    assert decision.outcome is AuthorizationOutcome.ALLOW
    assert decision.decision_reference == "authorization-001"
    client = client_type.instances[0]
    assert client.kwargs["scope_headers"] == {
        "X-AI-Governance-Organization-Id": "org_default",
        "X-AI-Governance-Project-Id": "project_default",
        "X-Correlation-Id": "corr-operations-test-001",
        "X-Request-Id": "langgraph-operations-test-001:authorize",
        "X-AI-Governance-Actor-Id": "runtime-service",
    }
    assert client.request.execution_id == "aigp-execution-001"
    assert client.request.action_id == "operations.update_status"
    assert client.request.resource.resource_reference == "operational-record:OPS-001"
    assert client.request.context == {}
    assert client.request.delegation_id == "delegation-001"
    assert client.request.tool_arguments_digest == "digest:OPS-001:MAINTENANCE"
    assert not hasattr(client.request, "actor_id")


def test_authoritative_adapter_uses_separate_issuer_credential_for_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def issue(url: str, headers: dict[str, str], payload: dict[str, object]):
        captured.update(url=url, headers=headers, payload=payload)
        return {
            "delegation_id": "delegation-001",
            "version": "1",
            "contract_digest": "sha256:" + "b" * 64,
        }

    monkeypatch.setattr("policy._post_json", issue)
    authorizer = AIGPRuntimeActionAuthorizer(
        base_url="http://governance.test",
        organization_id="org_default",
        project_id="project_default",
        expected_actor_id="runtime-service",
        access_token="runtime-token",
        delegation_issuer_access_token="issuer-token",
        delegation_issuer_actor_id="delegation-issuer",
    )

    bound = authorizer.bind_delegation(
        _request(OperationIntent.WRITE, governance_execution_id="aigp-execution-001")
    )

    assert captured["url"] == "http://governance.test/api/v1/runtime-action-delegations"
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": "Bearer issuer-token",
        "X-AI-Governance-Organization-Id": "org_default",
        "X-AI-Governance-Project-Id": "project_default",
        "X-AI-Governance-Actor-Id": "delegation-issuer",
        "X-Correlation-Id": "corr-operations-test-001",
        "X-Request-Id": "langgraph-operations-test-001:delegate",
    }
    assert captured["payload"]["runtime_actor_id"] == "runtime-service"  # type: ignore[index]
    assert bound.governance_delegation_digest == "sha256:" + "b" * 64


def test_authoritative_denial_cannot_mutate_sqlite_or_emit_fixture_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer, _client_type = _aigp_authorizer(monkeypatch, _aigp_decision("DENY"))
    transport = InMemoryAgentsRuntimeTransport()
    repository, dependencies = _dependencies(tmp_path, authorizer, _reporter(transport))

    result = run_workflow(_request(OperationIntent.WRITE), dependencies)

    assert result.outcome == "WRITE_DENIED"
    assert result.reason_code == "DENY_POLICY_MATCHED"
    assert repository.read("OPS-001")["status"] == "PENDING"
    assert not [
        payload
        for payload in transport.events_by_key.values()
        if payload["event_type"] in {"TOOL_CALL", "GOVERNANCE_DECISION"}
    ]


def test_invalid_authoritative_allow_cannot_mutate_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorizer, _client_type = _aigp_authorizer(
        monkeypatch,
        _aigp_decision("ALLOW"),
        reject_allow=True,
    )
    repository, dependencies = _dependencies(
        tmp_path,
        authorizer,
        _reporter(InMemoryAgentsRuntimeTransport()),
    )

    result = run_workflow(_request(OperationIntent.WRITE), dependencies)

    assert result.outcome == "GOVERNANCE_UNAVAILABLE"
    assert result.reason_code == "AUTHORIZATION_UNAVAILABLE"
    assert repository.read("OPS-001")["status"] == "PENDING"


def test_same_thread_different_runs_receive_isolated_aigp_executions(
    tmp_path: Path,
) -> None:
    transport = InvocationIdentityTransport()
    first_reporter = ExecutionEvidenceReporter(
        AgentsRuntimeClient(
            GovernanceSettings("http://governance.test", "org_default", "project_default"),
            transport=transport,
            sleep=lambda _seconds: None,
        ),
        external_execution_id="langgraph-run-1",
        correlation_id="langgraph-run-1",
    )
    second_reporter = ExecutionEvidenceReporter(
        AgentsRuntimeClient(
            GovernanceSettings("http://governance.test", "org_default", "project_default"),
            transport=transport,
            sleep=lambda _seconds: None,
        ),
        external_execution_id="langgraph-run-2",
        correlation_id="langgraph-run-2",
    )
    first_repository, first = _dependencies(
        tmp_path / "first", FixtureOperationAuthorizer(AuthorizationOutcome.ALLOW), first_reporter
    )
    second_repository, second = _dependencies(
        tmp_path / "second", FixtureOperationAuthorizer(AuthorizationOutcome.ALLOW), second_reporter
    )

    first_result = run_workflow(
        _request(
            OperationIntent.WRITE,
            external_execution_id="langgraph-run-1",
            correlation_id="langgraph-run-1",
            langgraph_thread_id="thread-1",
        ),
        first,
    )
    second_result = run_workflow(
        _request(
            OperationIntent.WRITE,
            external_execution_id="langgraph-run-2",
            correlation_id="langgraph-run-2",
            langgraph_thread_id="thread-1",
        ),
        second,
    )

    assert first_result.governance_execution_id != second_result.governance_execution_id
    assert first_repository.read("OPS-001")["status"] == "MAINTENANCE"
    assert second_repository.read("OPS-001")["status"] == "MAINTENANCE"
    registrations = [
        payload for url, _headers, payload in transport.requests if url.endswith("/agent-executions")
    ]
    assert [payload["external_execution_id"] for payload in registrations] == [
        "langgraph-run-1",
        "langgraph-run-2",
    ]
    assert {payload["metadata"]["langgraph_thread_id"] for payload in registrations} == {
        "thread-1"
    }


def test_async_invocation_preserves_evidence_and_terminal_lifecycle(tmp_path: Path) -> None:
    transport = InMemoryAgentsRuntimeTransport()
    repository, dependencies = _dependencies(
        tmp_path,
        FixtureOperationAuthorizer(AuthorizationOutcome.ALLOW),
        _reporter(transport),
    )

    result = asyncio.run(run_workflow_async(_request(OperationIntent.WRITE), dependencies))

    assert result.outcome == "WRITE_COMPLETED"
    assert result.evidence_status is EvidenceStatus.COMPLETED
    assert repository.read("OPS-001")["status"] == "MAINTENANCE"


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
