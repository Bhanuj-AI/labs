from __future__ import annotations

import httpx
from openai_agent_runtime.core_client import GovernanceCoreClient
from openai_agent_runtime.settings import GovernanceSettings


def test_core_client_forwards_an_explicit_bearer_token_only_as_auth_header() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, json={"audit_id": "audit-1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    core = GovernanceCoreClient(
        GovernanceSettings(
            base_url="https://core.example.test",
            organization_id="org-a",
            project_id="project-a",
            api_bearer_token="short-lived-token",
        ),
        replay_endpoint="https://runtime.example.test/replay",
        client=client,
    )

    assert core.get_causal_audit("audit-1") == {"audit_id": "audit-1"}
    assert captured["authorization"] == "Bearer short-lived-token"
    assert captured["x-ai-governance-organization-id"] == "org-a"
    assert captured["x-ai-governance-project-id"] == "project-a"


def test_core_client_reuses_only_an_exactly_matching_active_policy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/intervention-policies")
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "policy_id": "near-match",
                        "version": 1,
                        "status": "ACTIVE",
                        "tool_name": "transaction_risk.score",
                        "schema_id": "reference-openai-fraud-risk",
                        "schema_version": "v1",
                        "provider_id": "opaque-reference",
                        "provider_version": "v1",
                        "allowed_strategies": ["REPLACE"],
                        "strategy_configuration": {
                            "counterfactual_reference": "state://wrong",
                            "counterfactual_digest": "sha256:wrong",
                            "runtime_attests_validation": True,
                        },
                    },
                    {
                        "policy_id": "exact-match",
                        "version": 3,
                        "status": "ACTIVE",
                        "tool_name": "transaction_risk.score",
                        "schema_id": "reference-openai-fraud-risk",
                        "schema_version": "v1",
                        "provider_id": "opaque-reference",
                        "provider_version": "v1",
                        "allowed_strategies": ["REPLACE"],
                        "strategy_configuration": {
                            "counterfactual_reference": "state://counterfactual-low",
                            "counterfactual_digest": "sha256:expected",
                            "runtime_attests_validation": True,
                        },
                    },
                ]
            },
        )

    core = GovernanceCoreClient(
        GovernanceSettings("https://core.example.test", "org-a", "project-a"),
        replay_endpoint="https://runtime.example.test/replay",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert core.find_active_replacement_policy(
        counterfactual_reference="state://counterfactual-low",
        counterfactual_digest="sha256:expected",
    ) == ("exact-match", 3)
