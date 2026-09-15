"""The deliberately small OpenAI Agents SDK application boundary."""

from __future__ import annotations

from agents import Agent, ModelSettings, function_tool # type: ignore
from policy import lookup_policy_record


@function_tool
def lookup_policy(policy_number: str) -> str:
    """Look up the local status for a policy number."""
    return str(lookup_policy_record(policy_number)["status"])


def create_policy_validation_agent(model: str) -> Agent:
    """Build one agent and force its first turn to use the local function tool.

    The SDK's public ``reset_tool_choice=True`` default clears the named choice
    after ``lookup_policy`` runs, allowing the normal final model turn.
    """
    return Agent(
        name="Policy Validation Agent",
        model=model,
        instructions=(
            "Validate the requested policy. The first turn must use lookup_policy. "
            "After the tool result, answer with exactly: Policy <number>: <status>."
        ),
        tools=[lookup_policy],
        model_settings=ModelSettings(tool_choice="lookup_policy"),
        reset_tool_choice=True,
    )
