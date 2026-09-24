"""Public, bounded Agents Runtime client; never sends protected payloads to Core."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from openai_agent_runtime.contracts import ObservedExecution
from openai_agent_runtime.runtime import (
    ADAPTER_NAME,
    EVIDENCE_SCHEMA_ID,
    EVIDENCE_SCHEMA_VERSION,
    RUNTIME_TYPE,
)
from openai_agent_runtime.settings import GovernanceSettings


class CoreApiError(RuntimeError):
    """A bounded Core API failure that does not include response content."""


@dataclass(frozen=True)
class IngestedExecution:
    core_execution_id: str
    observed: ObservedExecution


class GovernanceCoreClient:
    """Use only published REST contracts for observed evidence and Causal Audit."""

    def __init__(
        self,
        settings: GovernanceSettings,
        *,
        replay_endpoint: str,
        client: httpx.Client | None = None,
    ) -> None:
        if not replay_endpoint.strip():
            raise CoreApiError(
                "OPENAI_AGENT_RUNTIME_PUBLIC_REPLAY_ENDPOINT is required."
            )
        self._settings = settings
        self._replay_endpoint = replay_endpoint.rstrip("/")
        self._client = client or httpx.Client(timeout=10.0)

    def ingest_observed_execution(
        self, observed: ObservedExecution
    ) -> IngestedExecution:
        created = self._request(
            "POST",
            "/api/v1/agent-executions",
            {
                "agent_id": "reference-openai-transaction-fraud-agent",
                "agent_name": "Reference OpenAI Transaction Fraud Agent",
                "agent_version": "1.0.0",
                "external_execution_id": observed.external_execution_id,
                "runtime_provider": RUNTIME_TYPE,
                "correlation_id": observed.external_execution_id,
                "metadata": {
                    "runtime_model": {
                        "model_id": observed.model_id,
                        "inference_configuration": dict(
                            observed.inference_configuration
                        ),
                    },
                    "replay_capability": {
                        "runtime_type": RUNTIME_TYPE,
                        "adapter_id": ADAPTER_NAME,
                        "adapter_version": "v1",
                        "replay_reference": observed.replay_reference,
                        "supported_interventions": ["REPLACE"],
                        "metadata": {"endpoint": self._replay_endpoint},
                    },
                },
            },
            expected_status=201,
        )
        try:
            execution_id = str(created["execution"]["execution_id"])
        except (KeyError, TypeError) as error:
            raise CoreApiError("Core did not return an execution ID.") from error
        self._request(
            "POST",
            f"/api/v1/agent-executions/{execution_id}/events",
            {
                "event_type": "TOOL_CALL",
                "actor_id": "transaction_risk.score",
                "actor_type": "TOOL",
                "tool_call_context": {
                    "schema_version": "1",
                    "runtime_tool_call_id": observed.runtime_tool_call_id,
                    "tool_call_group_id": "transaction-risk",
                    "depends_on_tool_call_ids": [],
                },
                "evidence_references": [observed.evidence_reference],
                "attributes": {
                    "tool": "transaction_risk.score",
                    "causal_replay": {
                        "evidence_descriptor": {
                            "tool_name": "transaction_risk.score",
                            "evidence_ref": observed.evidence_reference,
                            "evidence_digest": observed.evidence_digest,
                            "content_type": "application/vnd.openai-agent-runtime.evidence+json",
                            "schema_id": EVIDENCE_SCHEMA_ID,
                            "schema_version": EVIDENCE_SCHEMA_VERSION,
                            "replay_adapter_id": ADAPTER_NAME,
                            "metadata": {},
                        }
                    },
                },
            },
            expected_status=201,
        )
        self._request(
            "POST",
            f"/api/v1/agent-executions/{execution_id}/events",
            {
                "event_type": "EVALUATION",
                "actor_id": "openai-agent-runtime-outcome/v1",
                "actor_type": "EVALUATOR",
                "attributes": {"score": observed.outcome_score},
            },
            expected_status=201,
        )
        self._request(
            "POST",
            f"/api/v1/agent-executions/{execution_id}/complete",
            {"status": "SUCCEEDED"},
            expected_status=200,
        )
        return IngestedExecution(execution_id, observed)

    def create_and_activate_replacement_policy(
        self, *, counterfactual_reference: str, counterfactual_digest: str
    ) -> tuple[str, int]:
        policy = self._request(
            "POST",
            "/api/v1/agents-runtime/causal-audit/intervention-policies",
            {
                "tool_name": "transaction_risk.score",
                "schema_id": EVIDENCE_SCHEMA_ID,
                "schema_version": EVIDENCE_SCHEMA_VERSION,
                "allowed_strategies": ["REPLACE"],
                "provider_id": "opaque-reference",
                "provider_version": "v1",
                "strategy_configuration": {
                    "counterfactual_reference": counterfactual_reference,
                    "counterfactual_digest": counterfactual_digest,
                    "runtime_attests_validation": True,
                },
            },
            expected_status=201,
        )
        try:
            policy_id = str(policy["policy_id"])
            version = int(policy["version"])
        except (KeyError, TypeError, ValueError) as error:
            raise CoreApiError(
                "Core returned an invalid intervention policy."
            ) from error
        self._request(
            "POST",
            f"/api/v1/agents-runtime/causal-audit/intervention-policies/{policy_id}/versions/{version}/activate",
            {},
            expected_status=200,
        )
        return policy_id, version

    def find_active_replacement_policy(
        self, *, counterfactual_reference: str, counterfactual_digest: str
    ) -> tuple[str, int] | None:
        """Find the exact active policy required by this bounded fixture.

        This permits a repeatable local end-to-end run without relaxing Core's
        one-active-policy invariant.  A merely similar policy is never reused:
        every governed selector and the opaque-reference authorization must
        agree exactly.
        """
        result = self._request(
            "GET",
            "/api/v1/agents-runtime/causal-audit/intervention-policies",
            None,
            expected_status=200,
        )
        items = result.get("items")
        if not isinstance(items, list):
            raise CoreApiError("Core returned malformed intervention policies.")
        expected_configuration = {
            "counterfactual_reference": counterfactual_reference,
            "counterfactual_digest": counterfactual_digest,
            "runtime_attests_validation": True,
        }
        for item in items:
            if not isinstance(item, Mapping):
                continue
            if (
                item.get("status") == "ACTIVE"
                and item.get("tool_name") == "transaction_risk.score"
                and item.get("schema_id") == EVIDENCE_SCHEMA_ID
                and item.get("schema_version") == EVIDENCE_SCHEMA_VERSION
                and item.get("provider_id") == "opaque-reference"
                and item.get("provider_version") == "v1"
                and item.get("allowed_strategies") == ["REPLACE"]
                and item.get("strategy_configuration") == expected_configuration
            ):
                try:
                    return str(item["policy_id"]), int(item["version"])
                except (KeyError, TypeError, ValueError) as error:
                    raise CoreApiError(
                        "Core returned an invalid active intervention policy."
                    ) from error
        return None

    def submit_causal_audit(
        self, core_execution_id: str, policy_id: str, policy_version: int
    ) -> str:
        result = self._request(
            "POST",
            "/api/v1/agents-runtime/causal-audits",
            {
                "execution_id": core_execution_id,
                "evaluator_ref": "recorded-outcome/v1",
                "intervention": {
                    "strategy": "REPLACE",
                    "counterfactual_samples": 1,
                    "intervention_policy_id": policy_id,
                    "intervention_policy_version": policy_version,
                },
            },
            expected_status=202,
        )
        try:
            return str(result["audit_id"])
        except (KeyError, TypeError) as error:
            raise CoreApiError("Core did not return a Causal Audit ID.") from error

    def get_causal_audit(self, audit_id: str) -> Mapping[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/agents-runtime/causal-audits/{audit_id}",
            None,
            expected_status=200,
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None,
        *,
        expected_status: int,
    ) -> Mapping[str, Any]:
        headers = {
            "X-AI-Governance-Organization-Id": self._settings.organization_id,
            "X-AI-Governance-Project-Id": self._settings.project_id,
        }
        if self._settings.api_bearer_token:
            headers["Authorization"] = f"Bearer {self._settings.api_bearer_token}"
        try:
            response = self._client.request(
                method,
                f"{self._settings.base_url}{path}",
                headers=headers,
                json=payload,
            )
        except httpx.HTTPError as error:
            raise CoreApiError("Core request failed.") from error
        if response.status_code != expected_status:
            raise CoreApiError(f"Core request failed with HTTP {response.status_code}.")
        try:
            body = response.json()
        except ValueError as error:
            raise CoreApiError("Core returned malformed JSON.") from error
        if not isinstance(body, Mapping):
            raise CoreApiError("Core returned malformed JSON.")
        return body
