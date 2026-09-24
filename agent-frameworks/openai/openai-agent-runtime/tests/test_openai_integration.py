from __future__ import annotations

import os

import pytest
from openai_agent_runtime.contracts import ReplayEnvelope, Scenario
from openai_agent_runtime.runtime import (
    LOW_RISK_COUNTERFACTUAL,
    OpenAIAgentRuntime,
)
from openai_agent_runtime.settings import RuntimeSettings
from openai_agent_runtime.store import InMemoryExecutionStore


@pytest.mark.openai
def test_real_responses_api_replays_governed_low_risk_evidence() -> None:
    if os.getenv("RUN_OPENAI_INTEGRATION") != "1":
        pytest.skip("Set RUN_OPENAI_INTEGRATION=1 to make a real OpenAI API call.")
    settings = RuntimeSettings.from_environment()
    runtime = OpenAIAgentRuntime.from_settings(settings, InMemoryExecutionStore())

    observed = runtime.execute(Scenario.ALIGNED)
    replayed = runtime.replay(
        observed.replay_reference,
        ReplayEnvelope(
            policy_id="fraud-low-risk-policy",
            policy_version=1,
            external_execution_id=observed.external_execution_id,
            runtime_tool_call_id=observed.runtime_tool_call_id,
            intervention_provider="opaque-reference",
            intervention_provider_version="v1",
            strategy="REPLACE",
            original_evidence_digest=observed.evidence_digest,
            counterfactual_reference=LOW_RISK_COUNTERFACTUAL.reference,
            counterfactual_digest=LOW_RISK_COUNTERFACTUAL.digest,
            intervention_digest="openai-integration-fixture",
        ),
    )

    assert observed.runtime_tool_call_id
    assert observed.decision == "BLOCK"
    assert replayed.decision == "ALLOW"
    assert observed.outcome_score == 1.0
    assert replayed.outcome_score == 0.0


@pytest.mark.openai
def test_real_responses_api_replays_changed_evidence_but_locked_account_stays_blocked() -> (
    None
):
    if os.getenv("RUN_OPENAI_INTEGRATION") != "1":
        pytest.skip("Set RUN_OPENAI_INTEGRATION=1 to make a real OpenAI API call.")
    settings = RuntimeSettings.from_environment()
    runtime = OpenAIAgentRuntime.from_settings(settings, InMemoryExecutionStore())

    observed = runtime.execute(Scenario.IGNORED)
    replayed = runtime.replay(
        observed.replay_reference,
        ReplayEnvelope(
            policy_id="fraud-low-risk-policy",
            policy_version=1,
            external_execution_id=observed.external_execution_id,
            runtime_tool_call_id=observed.runtime_tool_call_id,
            intervention_provider="opaque-reference",
            intervention_provider_version="v1",
            strategy="REPLACE",
            original_evidence_digest=observed.evidence_digest,
            counterfactual_reference=LOW_RISK_COUNTERFACTUAL.reference,
            counterfactual_digest=LOW_RISK_COUNTERFACTUAL.digest,
            intervention_digest="openai-ignored-integration-fixture",
        ),
    )

    assert observed.runtime_tool_call_id
    assert replayed.counterfactual_evidence_digest != observed.evidence_digest
    assert observed.decision == replayed.decision == "BLOCK"
    assert observed.outcome_score == replayed.outcome_score == 1.0
