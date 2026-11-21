"""Minimal REST client for the live LangGraph ToolNode replay tutorial.

This client sends only bounded execution metadata, opaque evidence references,
and digests to AIGP OSS.  Private tool arguments and recorded evidence are
written only to the runtime-owned SQLite snapshot by ``run_live_lab.py``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AIGPError(RuntimeError):
    """The control plane rejected or could not receive a tutorial request."""


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
        values = {
            "AIGP_BASE_URL": os.getenv("AIGP_BASE_URL"),
            "AIGP_ORGANIZATION_ID": os.getenv("AIGP_ORGANIZATION_ID"),
            "AIGP_PROJECT_ID": os.getenv("AIGP_PROJECT_ID"),
            "AIGP_RUNTIME_ACTOR_ID": os.getenv("AIGP_RUNTIME_ACTOR_ID"),
            "AIGP_ISSUER_ACTOR_ID": os.getenv("AIGP_ISSUER_ACTOR_ID"),
            "AIGP_ISSUER_TOKEN": os.getenv("AIGP_ISSUER_TOKEN"),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise AIGPError(
                "Set " + ", ".join(missing) + " before running the live lab."
            )
        return cls(
            base_url=str(values["AIGP_BASE_URL"]).rstrip("/"),
            organization_id=str(values["AIGP_ORGANIZATION_ID"]),
            project_id=str(values["AIGP_PROJECT_ID"]),
            runtime_actor_id=str(values["AIGP_RUNTIME_ACTOR_ID"]),
            issuer_actor_id=str(values["AIGP_ISSUER_ACTOR_ID"]),
            issuer_token=str(values["AIGP_ISSUER_TOKEN"]),
            runtime_token=os.getenv("AIGP_RUNTIME_TOKEN"),
        )


class Client:
    def __init__(self, settings: Settings, correlation_id: str) -> None:
        self._settings = settings
        self._correlation_id = correlation_id

    def start_execution(
        self, external_execution_id: str, replay_capability: dict[str, object]
    ) -> str:
        response = self._post(
            "/api/v1/agent-executions",
            {
                "agent_id": "langgraph-tool-node-evidence-replay-v2",
                "agent_name": "LangGraph ToolNode evidence replay v2 lab",
                "agent_version": "2",
                "external_execution_id": external_execution_id,
                "runtime_provider": "langgraph",
                "correlation_id": self._correlation_id,
                "metadata": {
                    "lab": "langgraph-execution-assurance-v2",
                    "replay_capability": replay_capability,
                },
            },
            runtime=True,
        )
        return _string(response.get("execution"), "execution_id")

    def add_tool_evidence(
        self,
        execution_id: str,
        *,
        runtime_tool_call_id: str,
        evidence_reference: str,
        evidence_digest: str,
        tool_identity: dict[str, object],
        schema_id: str,
        schema_version: str,
    ) -> str:
        response = self._post(
            f"/api/v1/agent-executions/{execution_id}/events",
            {
                "event_type": "TOOL_CALL",
                "idempotency_key": f"{runtime_tool_call_id}:tool-evidence",
                "actor_id": "langgraph.tool-node",
                "actor_type": "TOOL",
                "tool_call_context": {
                    "schema_version": "1",
                    "runtime_tool_call_id": runtime_tool_call_id,
                    "tool_call_group_id": self._correlation_id,
                    "depends_on_tool_call_ids": [],
                },
                "evidence_references": [evidence_reference],
                "attributes": {
                    "tool_name": "account_lookup",
                    "outcome": "SUCCEEDED",
                    "causal_replay": {
                        "evidence_descriptor": {
                            "tool_name": "account_lookup",
                            "evidence_ref": evidence_reference,
                            "evidence_digest": evidence_digest,
                            "content_type": "application/json",
                            "schema_id": schema_id,
                            "schema_version": schema_version,
                            "replay_adapter_id": "langgraph/v2",
                            "metadata": {
                                "execution_replay_tool_identity": tool_identity
                            },
                        }
                    },
                },
            },
            runtime=True,
        )
        return _string(response.get("event"), "event_id")

    def add_baseline_score(self, execution_id: str) -> None:
        self._post(
            f"/api/v1/agent-executions/{execution_id}/events",
            {
                "event_type": "EVALUATION",
                "idempotency_key": f"{self._correlation_id}:baseline-score",
                "actor_id": "tutorial-outcome-evaluator",
                "actor_type": "EVALUATOR",
                "attributes": {"score": 1.0},
            },
            runtime=True,
        )

    def complete(self, execution_id: str) -> None:
        self._post(
            f"/api/v1/agent-executions/{execution_id}/complete",
            {"status": "SUCCEEDED"},
            runtime=True,
        )

    def eligibility(
        self,
        execution_id: str,
        *,
        policy_id: str | None = None,
        policy_version: int | None = None,
    ) -> dict[str, object]:
        query = ""
        if policy_id is not None and policy_version is not None:
            query = (
                f"?intervention_policy_id={policy_id}"
                f"&intervention_policy_version={policy_version}"
            )
        return self._get(
            "/api/v1/agents-runtime/executions/"
            f"{execution_id}/causal-audit-eligibility{query}",
            runtime=True,
        )

    def create_policy(
        self,
        *,
        replacement_reference: str,
        replacement_digest: str,
        schema_id: str,
        schema_version: str,
    ) -> dict[str, object]:
        return self._post(
            "/api/v1/agents-runtime/causal-audit/intervention-policies",
            {
                "tool_name": "account_lookup",
                "schema_id": schema_id,
                "schema_version": schema_version,
                "provider_id": "opaque-reference",
                "provider_version": "v1",
                "allowed_strategies": ["REPLACE"],
                "strategy_configuration": {
                    "counterfactual_reference": replacement_reference,
                    "counterfactual_digest": replacement_digest,
                    "runtime_attests_validation": True,
                },
            },
            issuer=True,
        )

    def validate_and_activate_policy(
        self, policy_id: str, version: int
    ) -> dict[str, object]:
        path = (
            "/api/v1/agents-runtime/causal-audit/intervention-policies/"
            f"{policy_id}/versions/{version}"
        )
        self._post(f"{path}/validate", {}, issuer=True)
        return self._post(f"{path}/activate", {}, issuer=True)

    def start_causal_audit(
        self, execution_id: str, policy_id: str, policy_version: int
    ) -> dict[str, object]:
        return self._post(
            "/api/v1/agents-runtime/causal-audits",
            {
                "execution_id": execution_id,
                "evaluator_ref": "recorded-outcome/v1",
                "intervention": {
                    "strategy": "REPLACE",
                    "counterfactual_samples": 1,
                    "intervention_policy_id": policy_id,
                    "intervention_policy_version": policy_version,
                },
            },
            runtime=True,
        )

    def active_policy(
        self,
        *,
        replacement_reference: str,
        replacement_digest: str,
        schema_id: str,
        schema_version: str,
    ) -> dict[str, object] | None:
        response = self._get(
            "/api/v1/agents-runtime/causal-audit/intervention-policies", runtime=True
        )
        items = response.get("items")
        if not isinstance(items, list):
            raise AIGPError("AIGP intervention policy list was invalid.")
        expected = {
            "counterfactual_reference": replacement_reference,
            "counterfactual_digest": replacement_digest,
            "runtime_attests_validation": True,
        }
        for item in items:
            if not isinstance(item, dict):
                continue
            if (
                item.get("status") == "ACTIVE"
                and item.get("tool_name") == "account_lookup"
                and item.get("schema_id") == schema_id
                and item.get("schema_version") == schema_version
                and item.get("provider_id") == "opaque-reference"
                and item.get("provider_version") == "v1"
                and item.get("strategy_configuration") == expected
            ):
                return item
        return None

    def causal_audit(self, audit_id: str) -> dict[str, object]:
        return self._get(
            f"/api/v1/agents-runtime/causal-audits/{audit_id}", runtime=True
        )

    def _headers(self, *, issuer: bool, runtime: bool) -> dict[str, str]:
        del runtime
        actor_id = (
            self._settings.issuer_actor_id
            if issuer
            else self._settings.runtime_actor_id
        )
        token = (
            self._settings.issuer_token
            if issuer
            else self._settings.runtime_token or self._settings.issuer_token
        )
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-AI-Governance-Organization-Id": self._settings.organization_id,
            "X-AI-Governance-Project-Id": self._settings.project_id,
            "X-AI-Governance-Actor-Id": actor_id,
            "X-Correlation-Id": self._correlation_id,
            "X-Request-Id": self._correlation_id,
        }

    def _post(
        self,
        path: str,
        payload: dict[str, object],
        *,
        issuer: bool = False,
        runtime: bool = False,
    ) -> dict[str, object]:
        request = Request(
            self._settings.base_url + path,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=self._headers(issuer=issuer, runtime=runtime),
            method="POST",
        )
        return self._read(request)

    def _get(self, path: str, *, runtime: bool) -> dict[str, object]:
        return self._read(
            Request(
                self._settings.base_url + path,
                headers=self._headers(issuer=False, runtime=runtime),
            )
        )

    @staticmethod
    def _read(request: Request) -> dict[str, object]:
        try:
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise AIGPError(f"AIGP request failed with HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise AIGPError(f"AIGP request could not be sent: {error.reason}") from error
        if not isinstance(payload, dict):
            raise AIGPError("AIGP response was not a JSON object.")
        return payload


def _string(value: object, name: str) -> str:
    if not isinstance(value, dict):
        raise AIGPError(f"AIGP response lacked {name}.")
    result = value.get(name)
    if not isinstance(result, str) or not result:
        raise AIGPError(f"AIGP response lacked {name}.")
    return result
