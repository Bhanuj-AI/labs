"""Small REST adapter for public AIGP runtime contracts used by the lab."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class AIGPError(RuntimeError):
    pass


def digest(value: dict[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Settings:
    base_url: str
    organization_id: str
    project_id: str
    runtime_actor_id: str
    issuer_actor_id: str
    issuer_token: str
    runtime_token: str | None = None

    @classmethod
    def from_environment(cls) -> Settings:
        required = {
            "AIGP_BASE_URL": os.getenv("AIGP_BASE_URL"),
            "AIGP_ORGANIZATION_ID": os.getenv("AIGP_ORGANIZATION_ID"),
            "AIGP_PROJECT_ID": os.getenv("AIGP_PROJECT_ID"),
            "AIGP_RUNTIME_ACTOR_ID": os.getenv("AIGP_RUNTIME_ACTOR_ID"),
            "AIGP_ISSUER_ACTOR_ID": os.getenv("AIGP_ISSUER_ACTOR_ID"),
            "AIGP_ISSUER_TOKEN": os.getenv("AIGP_ISSUER_TOKEN"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise AIGPError("Set " + ", ".join(missing) + " for the live OSS lab.")
        return cls(
            str(required["AIGP_BASE_URL"]).rstrip("/"),
            str(required["AIGP_ORGANIZATION_ID"]),
            str(required["AIGP_PROJECT_ID"]),
            str(required["AIGP_RUNTIME_ACTOR_ID"]),
            str(required["AIGP_ISSUER_ACTOR_ID"]),
            str(required["AIGP_ISSUER_TOKEN"]),
            os.getenv("AIGP_RUNTIME_TOKEN"),
        )


class Client:
    def __init__(self, settings: Settings, correlation_id: str) -> None:
        self.settings = settings
        self.correlation_id = correlation_id

    def start_execution(self, external_id: str, *, invalid_manifest: bool = False) -> str:
        response = self._post("/api/v1/agent-executions", {
            "agent_id": "langgraph-execution-assurance",
            "agent_name": "LangGraph banking execution assurance lab",
            "agent_version": "1",
            "external_execution_id": external_id,
            "runtime_provider": "langgraph",
            "correlation_id": self.correlation_id,
            "metadata": {"lab": "langgraph-execution-assurance", "replay_capability": ({
                "runtime_type": "langgraph", "adapter_id": "langgraph/v1",
                "adapter_version": "1", "replay_reference": f"langgraph://replays/{external_id}",
                "isolation_guarantee": "sqlite-copy/v1", "supported_interventions": ["REPLACE"]} if not invalid_manifest else {"runtime_type": "langgraph"})},
        }, runtime=True)
        return str(response["execution"]["execution_id"])

    def issue_delegation(self, execution_id: str, loan_id: str) -> dict[str, Any]:
        return self._post("/api/v1/runtime-action-delegations", {
            "schema_version": "runtime-action-delegation/v1", "execution_id": execution_id,
            "runtime_actor_id": self.settings.runtime_actor_id,
            "permissions": [{"action_id": "banking.loan.approve", "resource_type": "sqlite.row", "resource_references": [f"loan:{loan_id}"]}],
            "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "idempotency_key": f"{self.correlation_id}:delegation",
        }, issuer=True)

    def revoke(self, delegation_id: str) -> None:
        self._post(f"/api/v1/runtime-action-delegations/{delegation_id}/revoke", {"revocation_reference": f"{self.correlation_id}:revoke"}, issuer=True)

    def authorize(self, execution_id: str, delegation: dict[str, Any], arguments: dict[str, object]) -> dict[str, Any]:
        return self._post("/api/v1/runtime-action-authorizations/authorize", {
            "schema_version": "runtime-action-authorization/v2", "execution_id": execution_id,
            "correlation_id": self.correlation_id, "action_id": "banking.loan.approve",
            "resource": {"resource_type": "sqlite.row", "resource_reference": f"loan:{arguments['loan_id']}"},
            "delegation_id": delegation["delegation_id"], "delegation_version": delegation["version"],
            "delegation_digest": delegation["contract_digest"], "tool_arguments_digest": digest(arguments),
            "context": {}, "idempotency_key": f"{self.correlation_id}:authorize",
        }, runtime=True)

    def receipt(self, execution_id: str, decision: dict[str, Any], arguments: dict[str, object], status: str, *, supersedes: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"schema_version": "runtime-action-execution-receipt/v1", "decision_id": decision["decision_id"], "request_fingerprint": decision["request_fingerprint"], "action_id": "banking.loan.approve", "runtime_tool_call_id": f"{self.correlation_id}:approve", "tool_arguments_digest": digest(arguments), "status": status, "reason_code": f"LAB_{status}", "idempotency_key": f"{self.correlation_id}:receipt:{status.lower()}"}
        if status == "SUCCEEDED":
            payload.update({"result_reference": f"sqlite://loan/{arguments['loan_id']}", "result_digest": digest({"status": "APPROVED"})})
        if supersedes:
            payload["supersedes_receipt_id"] = supersedes
        return self._post(f"/api/v1/agent-executions/{execution_id}/runtime-action-receipts", payload, runtime=True)["event"]

    def tool_evidence(self, execution_id: str, decision_id: str, arguments: dict[str, object]) -> None:
        tool_id = f"{self.correlation_id}:approve"
        self._post(f"/api/v1/agent-executions/{execution_id}/events", {
            "event_type": "TOOL_CALL", "idempotency_key": f"{tool_id}:tool",
            "causation_id": decision_id, "actor_id": "banking.sqlite", "actor_type": "TOOL",
            "tool_call_context": {"schema_version": "1", "runtime_tool_call_id": tool_id, "tool_call_group_id": f"{self.correlation_id}:loan", "depends_on_tool_call_ids": []},
            "evidence_references": [f"sqlite://loan/{arguments['loan_id']}"],
            "attributes": {"tool_name": "banking.loan.approve", "outcome": "SUCCEEDED", "causal_replay": {"evidence_descriptor": {"tool_name": "banking.loan.approve", "evidence_ref": f"sqlite://loan/{arguments['loan_id']}", "evidence_digest": digest({"loan_id": arguments["loan_id"], "status": "APPROVED"}), "content_type": "application/json", "schema_id": "banking-loan-status", "schema_version": "1", "replay_adapter_id": "langgraph/v1", "metadata": {}}}},
        }, runtime=True)
        self._post(f"/api/v1/agent-executions/{execution_id}/events", {"event_type": "EVALUATION", "idempotency_key": f"{tool_id}:evaluation", "actor_id": "banking-evaluator", "actor_type": "EVALUATOR", "attributes": {"score": 1.0}}, runtime=True)

    def causal_eligibility(self, execution_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/agents-runtime/executions/{execution_id}/causal-audit-eligibility", runtime=True)

    def start_causal_audit(
        self,
        execution_id: str,
        policy_id: str,
        policy_version: int,
    ) -> dict[str, Any]:
        return self._post("/api/v1/agents-runtime/causal-audits", {
            "execution_id": execution_id,
            "evaluator_ref": "recorded-outcome/v1",
            "intervention": {
                "strategy": "REPLACE",
                "counterfactual_samples": 1,
                "intervention_policy_id": policy_id,
                "intervention_policy_version": policy_version,
            },
        }, runtime=True)

    def complete(self, execution_id: str, status: str) -> None:
        self._post(f"/api/v1/agent-executions/{execution_id}/complete", {"status": status}, runtime=True)

    def detail(self, execution_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/agent-executions/{execution_id}", runtime=True)

    def _headers(self, *, issuer: bool, runtime: bool) -> dict[str, str]:
        actor = self.settings.issuer_actor_id if issuer else self.settings.runtime_actor_id
        token = self.settings.issuer_token if issuer else self.settings.runtime_token
        headers = {"Content-Type": "application/json", "Accept": "application/json", "X-AI-Governance-Organization-Id": self.settings.organization_id, "X-AI-Governance-Project-Id": self.settings.project_id, "X-AI-Governance-Actor-Id": actor, "X-Correlation-Id": self.correlation_id, "X-Request-Id": self.correlation_id}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _post(self, path: str, payload: dict[str, Any], *, issuer: bool = False, runtime: bool = False) -> dict[str, Any]:
        request = Request(self.settings.base_url + path, data=json.dumps(payload).encode(), headers=self._headers(issuer=issuer, runtime=runtime), method="POST")
        return self._read(request)

    def _get(self, path: str, *, runtime: bool = False) -> dict[str, Any]:
        return self._read(Request(self.settings.base_url + path, headers=self._headers(issuer=False, runtime=runtime)))

    def _read(self, request: Request) -> dict[str, Any]:
        try:
            with urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode())
        except HTTPError as error:
            raise AIGPError(error.read().decode()) from error
        if not isinstance(data, dict):
            raise AIGPError("AIGP response was not an object.")
        return data
