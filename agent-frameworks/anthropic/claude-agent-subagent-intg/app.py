"""Run one Claude main-agent and research-subagent policy workflow."""

from __future__ import annotations

import asyncio
import os

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage

from agent import USER_PROMPT, create_policy_options
from governance import create_hooks, create_reporter, new_session_id


async def run() -> tuple[str, str, str | None, str]:
    model = os.getenv("CLAUDE_MODEL")
    if not model:
        raise RuntimeError("CLAUDE_MODEL is required. Set it in your environment.")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required. Set it in your environment.")

    session_id = new_session_id()
    reporter = create_reporter(session_id)
    options = create_policy_options(model, create_hooks(reporter))
    result: ResultMessage | None = None
    reporter.workflow_started()
    try:
        async with ClaudeSDKClient(options) as client:
            await client.query(USER_PROMPT, session_id=session_id)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    reporter.model_completed(message)
                elif isinstance(message, ResultMessage):
                    result = message
        if result is None:
            raise RuntimeError("Claude Agent SDK completed without a result message.")
        if result.is_error:
            reporter.workflow_failed()
            return result.result or "", session_id, reporter.execution_id, _evidence_status(reporter)
        reporter.workflow_completed()
        return result.result or "", session_id, reporter.execution_id, _evidence_status(reporter)
    except Exception:
        reporter.workflow_failed()
        raise


def _evidence_status(reporter: object) -> str:
    if not getattr(reporter, "enabled"):
        return "disabled"
    if getattr(reporter, "execution_id") and not getattr(reporter, "delivery_failed"):
        return "delivered"
    return "unavailable (Claude execution was not affected)"


def main() -> None:
    answer, session_id, execution_id, evidence_status = asyncio.run(run())
    print("Policy: POL-1001")
    print(f"Claude session ID: {session_id}")
    print(f"Final answer: {answer}")
    print(f"BHANUJ Platform evidence: {evidence_status}")
    if execution_id:
        print(f"BHANUJ Platform execution ID: {execution_id}")


if __name__ == "__main__":
    main()
