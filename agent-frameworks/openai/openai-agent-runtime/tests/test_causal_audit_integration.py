from __future__ import annotations

import json
import os
import time

import pytest
from openai_agent_runtime.contracts import Scenario
from openai_agent_runtime.core_client import GovernanceCoreClient
from openai_agent_runtime.runtime import (
    LOW_RISK_COUNTERFACTUAL,
    OpenAIAgentRuntime,
)
from openai_agent_runtime.settings import GovernanceSettings, RuntimeSettings
from openai_agent_runtime.store import FileExecutionStore


@pytest.mark.openai
@pytest.mark.integration
def test_real_openai_runtime_completes_the_public_core_causal_audit_path() -> None:
    if os.getenv("RUN_OPENAI_CAUSAL_AUDIT") != "1":
        pytest.skip(
            "Set RUN_OPENAI_CAUSAL_AUDIT=1 after starting Core, its plugin-enabled worker, and this runtime server."
        )
    runtime_settings = RuntimeSettings.from_environment()
    if not runtime_settings.public_replay_endpoint:
        pytest.fail("OPENAI_AGENT_RUNTIME_PUBLIC_REPLAY_ENDPOINT is required.")
    runtime = OpenAIAgentRuntime.from_settings(
        runtime_settings, FileExecutionStore(runtime_settings.state_dir)
    )
    core = GovernanceCoreClient(
        GovernanceSettings.from_environment(),
        replay_endpoint=runtime_settings.public_replay_endpoint,
    )
    policy = core.find_active_replacement_policy(
        counterfactual_reference=LOW_RISK_COUNTERFACTUAL.reference,
        counterfactual_digest=LOW_RISK_COUNTERFACTUAL.digest,
    )
    policy_id, policy_version = policy or core.create_and_activate_replacement_policy(
        counterfactual_reference=LOW_RISK_COUNTERFACTUAL.reference,
        counterfactual_digest=LOW_RISK_COUNTERFACTUAL.digest,
    )

    aligned = runtime.execute(Scenario.ALIGNED)
    ignored = runtime.execute(Scenario.IGNORED)
    aligned_audit = _submit_and_wait(core, aligned, policy_id, policy_version)
    ignored_audit = _submit_and_wait(core, ignored, policy_id, policy_version)

    assert aligned.decision == "BLOCK"
    assert aligned_audit["classification"] == "EVIDENCE_ALIGNED"
    assert ignored_audit["classification"] == "EVIDENCE_IGNORED"
    for audit in (aligned_audit, ignored_audit):
        result = audit["tool_call_results"][0]
        lineage = result["counterfactual_lineage"][0]
        assert lineage["policy_id"] == policy_id
        assert lineage["policy_version"] == policy_version
        assert (
            lineage["counterfactual_evidence_reference"]
            == LOW_RISK_COUNTERFACTUAL.reference
        )
        assert (
            lineage["counterfactual_evidence_digest"] == LOW_RISK_COUNTERFACTUAL.digest
        )
        assert lineage["replay_status"] == "EXECUTION_COMPLETED"
    print(
        json.dumps(
            {
                "policy": {"id": policy_id, "version": policy_version},
                "aligned": _safe_summary(aligned, aligned_audit),
                "ignored": _safe_summary(ignored, ignored_audit),
            },
            sort_keys=True,
        )
    )


def _submit_and_wait(
    core: GovernanceCoreClient, observed, policy_id: str, policy_version: int
):
    ingested = core.ingest_observed_execution(observed)
    audit_id = core.submit_causal_audit(
        ingested.core_execution_id, policy_id, policy_version
    )
    deadline = time.monotonic() + 120
    while True:
        audit = core.get_causal_audit(audit_id)
        if audit["status"] == "SUCCEEDED":
            return audit
        if audit["status"] in {"FAILED", "CANCELLED"}:
            pytest.fail(f"Causal Audit ended {audit['status']}.")
        if time.monotonic() >= deadline:
            pytest.fail("Causal Audit did not reach a terminal state.")
        time.sleep(1)


def _safe_summary(observed, audit) -> dict[str, object]:
    return {
        "audit_id": audit["audit_id"],
        "classification": audit["classification"],
        "decision": observed.decision,
        "evidence_digest": observed.evidence_digest,
        "external_execution_id": observed.external_execution_id,
        "model_id": observed.model_id,
        "outcome_score": observed.outcome_score,
        "runtime_tool_call_id": observed.runtime_tool_call_id,
    }
