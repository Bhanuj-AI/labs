"""A deliberately small, native OpenAI Agents SDK handoff workflow."""

from __future__ import annotations

from agents import Agent, ModelSettings, function_tool, handoff  # type: ignore

from policy import lookup_policy_record


TRIAGE_AGENT_NAME = "Claims Triage Agent"
POLICY_AGENT_NAME = "Policy Validation Agent"
HANDOFF_TOOL_NAME = "transfer_to_policy_validation"


@function_tool
def lookup_policy(policy_number: str) -> str:
    """Look up the local status for a policy number."""
    return str(lookup_policy_record(policy_number)["status"])


def create_claims_agents(model: str | object) -> tuple[Agent, Agent]:
    """Create a triage agent and its specialist using public handoff support."""
    policy_agent = Agent(
        name=POLICY_AGENT_NAME,
        model=model,
        instructions=(
            "Validate the requested policy. Use lookup_policy, then answer exactly: "
            "Policy <number>: <status>."
        ),
        tools=[lookup_policy],
        model_settings=ModelSettings(tool_choice="lookup_policy"),
        reset_tool_choice=True,
    )
    triage_agent = Agent(
        name=TRIAGE_AGENT_NAME,
        model=model,
        instructions="Transfer every policy-validation request to the policy specialist.",
        handoffs=[
            handoff(
                policy_agent,
                tool_name_override=HANDOFF_TOOL_NAME,
                tool_description_override="Transfer this policy request to the validation specialist.",
            )
        ],
        model_settings=ModelSettings(tool_choice=HANDOFF_TOOL_NAME),
        reset_tool_choice=True,
    )
    return triage_agent, policy_agent
