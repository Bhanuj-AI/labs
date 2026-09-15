"""Public Claude Agent SDK configuration for one main agent and one subagent."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, create_sdk_mcp_server

from policy import lookup_policy


POLICY_TOOL_NAME = "mcp__policy__lookup_policy"
RESEARCH_SUBAGENT_TYPE = "research-policy-subagent"

MAIN_SYSTEM_PROMPT = """You are the main insurance claim assistant.
Delegate the policy lookup to Research Subagent exactly once. Do not call
lookup_policy yourself. After the subagent returns, use its finding to give a
short answer stating whether policy POL-1001 lets the claim proceed."""

RESEARCH_SUBAGENT_PROMPT = """You are Research Subagent. Use lookup_policy exactly
once for policy_id POL-1001. Return a concise factual policy finding to the
main agent. Do not delegate work or use any other tool."""

USER_PROMPT = "Ask Research Subagent whether policy POL-1001 is active and tell me whether the claim can proceed."


def create_policy_options(model: str, hooks: dict[str, list[Any]]) -> ClaudeAgentOptions:
    """Configure the main Agent tool and the subagent's sole policy tool."""
    policy_server = create_sdk_mcp_server(
        name="policy",
        version="1.0.0",
        tools=[lookup_policy],
    )
    research_subagent = AgentDefinition(
        description="Researches the deterministic local policy record for an insurance claim.",
        prompt=RESEARCH_SUBAGENT_PROMPT,
        # The subagent cannot spawn agents and may call only the local MCP tool.
        tools=[POLICY_TOOL_NAME],
        mcpServers=["policy"],
        model="inherit",
        maxTurns=3,
        permissionMode="dontAsk",
    )
    return ClaudeAgentOptions(
        model=model,
        system_prompt=MAIN_SYSTEM_PROMPT,
        # Restrict built-ins to Agent. The policy tool is available only to the
        # declared subagent via its AgentDefinition.tools list.
        tools=["Agent"],
        allowed_tools=["Agent", POLICY_TOOL_NAME],
        mcp_servers={"policy": policy_server},
        agents={RESEARCH_SUBAGENT_TYPE: research_subagent},
        strict_mcp_config=True,
        permission_mode="dontAsk",
        setting_sources=[],
        skills=[],
        plugins=[],
        hooks=hooks,
    )
