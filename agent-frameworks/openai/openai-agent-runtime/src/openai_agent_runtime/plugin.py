"""Plugin registration only; Core never imports this OpenAI-owned package."""

from __future__ import annotations

from bhanuj_governance_plugin_api import (
    PluginMetadata,
    ReplayExecutionAdapterContribution,
)

from openai_agent_runtime.core_replay_adapter import (
    OpenAIAgentRuntimeReplayAdapter,
)


class OpenAIAgentRuntimePlugin:
    """Contribute the versioned replay adapter to a plugin-enabled worker."""

    metadata = PluginMetadata(
        name="openai-agent-runtime",
        version="0.1.0",
        required_ai_governance_version=">=1.1,<2",
        capabilities=("replay.execute",),
        spi_version="1",
    )

    def validate(self, context) -> None:
        del context

    def register(self, context) -> None:
        context.contributions.replay_execution_adapters(
            (
                ReplayExecutionAdapterContribution(
                    "openai-agent-runtime",
                    "v1",
                    OpenAIAgentRuntimeReplayAdapter(),
                ),
            )
        )

    def start(self, context) -> None:
        del context

    def stop(self, context) -> None:
        del context
