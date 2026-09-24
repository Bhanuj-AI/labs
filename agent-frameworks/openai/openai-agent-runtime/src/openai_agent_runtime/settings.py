"""Environment-owned runtime settings; secrets are never serialised or logged."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class RuntimeConfigurationError(ValueError):
    """A required runtime setting is absent or unsafe."""


@dataclass(frozen=True)
class RuntimeSettings:
    api_key: str
    model: str
    state_dir: Path
    replay_auth_token: str
    public_replay_endpoint: str | None
    response_continuation: str

    @classmethod
    def from_environment(cls) -> RuntimeSettings:
        api_key = _required("OPENAI_API_KEY")
        model = _required("OPENAI_MODEL")
        token = _required("OPENAI_AGENT_RUNTIME_REPLAY_AUTH_TOKEN")
        state_dir = Path(
            os.getenv("OPENAI_AGENT_RUNTIME_STATE_DIR", "./runtime-state")
        ).expanduser()
        endpoint = os.getenv("OPENAI_AGENT_RUNTIME_PUBLIC_REPLAY_ENDPOINT", "").strip()
        response_continuation = os.getenv(
            "OPENAI_AGENT_RUNTIME_RESPONSE_CONTINUATION", "local"
        ).strip()
        if response_continuation not in {"local", "provider_state"}:
            raise RuntimeConfigurationError(
                "OPENAI_AGENT_RUNTIME_RESPONSE_CONTINUATION must be 'local' or "
                "'provider_state'."
            )
        return cls(
            api_key=api_key,
            model=model,
            state_dir=state_dir,
            replay_auth_token=token,
            public_replay_endpoint=endpoint or None,
            response_continuation=response_continuation,
        )


@dataclass(frozen=True)
class GovernanceSettings:
    base_url: str
    organization_id: str
    project_id: str
    api_bearer_token: str | None = None

    @classmethod
    def from_environment(cls) -> GovernanceSettings:
        return cls(
            base_url=_required("AI_GOVERNANCE_BASE_URL").rstrip("/"),
            organization_id=_required("AI_GOVERNANCE_ORGANIZATION_ID"),
            project_id=_required("AI_GOVERNANCE_PROJECT_ID"),
            # Core deployments that protect their public API require a bearer
            # token.  Keep acquisition outside this reference runtime so it
            # never owns client credentials or an identity-provider protocol.
            api_bearer_token=os.getenv("AI_GOVERNANCE_API_BEARER_TOKEN", "").strip()
            or None,
        )


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeConfigurationError(f"{name} is required.")
    return value
