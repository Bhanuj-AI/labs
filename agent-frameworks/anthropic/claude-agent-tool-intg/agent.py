"""Public Claude Agent SDK configuration for the policy-validation example."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server

from policy import lookup_policy


POLICY_TOOL_NAME = "mcp__policy__lookup_policy"

SYSTEM_PROMPT = """You are a policy validation assistant.
For the user's request, invoke lookup_policy exactly once with policy_id POL-1001.
Then give a short, direct answer that states whether the claim can proceed.
Do not claim a policy fact before using the tool."""

USER_PROMPT = "Check whether policy POL-1001 is active and tell me whether the claim can proceed."


def create_policy_options(model: str, hooks: dict[str, list[Any]]) -> ClaudeAgentOptions:
    """Expose exactly one application tool and no Claude built-in tools."""
    policy_server = create_sdk_mcp_server(
        name="policy",
        version="1.0.0",
        tools=[lookup_policy],
    )
    return ClaudeAgentOptions(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        # An empty list disables the SDK's built-in tool set. allowed_tools only
        # approves this MCP tool; it is not used as a tool-surface filter.
        tools=[],
        allowed_tools=[POLICY_TOOL_NAME],
        mcp_servers={"policy": policy_server},
        strict_mcp_config=True,
        permission_mode="dontAsk",
        setting_sources=[],
        skills=[],
        plugins=[],
        hooks=hooks,
    )
