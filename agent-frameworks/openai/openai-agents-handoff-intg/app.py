"""Run one native OpenAI Agents SDK handoff workflow."""

from __future__ import annotations

import asyncio
import os

from agents import Runner  # type: ignore

from agent import create_claims_agents
from governance import EvidenceStatus, ExecutionEvidenceReporter, GovernedRunHooks


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required to run this OpenAI Agents SDK lab.")
    model = os.getenv("OPENAI_MODEL")
    if not model:
        raise RuntimeError("OPENAI_MODEL is required to run this OpenAI Agents SDK lab.")

    triage_agent, _ = create_claims_agents(model)
    reporter = ExecutionEvidenceReporter.from_environment()
    reporter.begin()
    try:
        result = asyncio.run(
            Runner.run(
                triage_agent,
                "Validate policy POL-100.",
                hooks=GovernedRunHooks(reporter),
            )
        )
    except Exception:
        reporter.fail_active_agent()
        reporter.complete("FAILED")
        raise
    reporter.complete("SUCCEEDED")

    print(f"Agent result: {result.final_output}")
    if reporter.status is not EvidenceStatus.DISABLED:
        print(f"BHANUJ execution ID: {reporter.execution_id or 'unavailable'}")
        print(f"BHANUJ evidence status: {reporter.status.value}")
        if reporter.error:
            print(f"BHANUJ evidence error: {reporter.error}")


if __name__ == "__main__":
    main()
