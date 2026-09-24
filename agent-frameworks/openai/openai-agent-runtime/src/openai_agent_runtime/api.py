"""Private runtime HTTP boundary used only by the allow-listed Core adapter."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, status  # type: ignore
from pydantic import BaseModel, ConfigDict, Field  # type: ignore

from openai_agent_runtime.contracts import (
    ReplayEnvelope,
    Scenario,
)
from openai_agent_runtime.runtime import (
    OpenAIAgentRuntime,
    RuntimeInvocationError,
)
from openai_agent_runtime.settings import RuntimeSettings
from openai_agent_runtime.store import FileExecutionStore


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Scenario = Scenario.ALIGNED


class ReplayEnvelopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["replay-intervention-envelope/v1"]
    policy_id: str = Field(min_length=1)
    policy_version: int = Field(ge=1)
    external_execution_id: str = Field(min_length=1)
    runtime_tool_call_id: str = Field(min_length=1)
    intervention_provider: str = Field(min_length=1)
    intervention_provider_version: str = Field(min_length=1)
    strategy: Literal["NULLIFY", "REPLACE", "PERTURB"]
    original_evidence_digest: str = Field(min_length=1)
    counterfactual_reference: str = Field(min_length=1)
    counterfactual_digest: str = Field(min_length=1)
    intervention_digest: str = Field(min_length=1)

    def as_runtime_envelope(self) -> ReplayEnvelope:
        return ReplayEnvelope(**self.model_dump())


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replay_reference: str = Field(min_length=1)
    envelope: ReplayEnvelopeRequest


def create_app(
    runtime: OpenAIAgentRuntime | None = None,
    replay_auth_token: str | None = None,
) -> FastAPI:
    """Build the runtime app; tests may inject a credential-free fake runtime."""
    if runtime is None:
        settings = RuntimeSettings.from_environment()
        runtime = OpenAIAgentRuntime.from_settings(
            settings, FileExecutionStore(settings.state_dir)
        )
        replay_auth_token = settings.replay_auth_token
    if not replay_auth_token:
        raise RuntimeInvocationError(
            "OPENAI_AGENT_RUNTIME_REPLAY_AUTH_TOKEN is required."
        )

    app = FastAPI(title="Reference OpenAI Agent Runtime", version="0.1.0")

    @app.get("/health")
    def health() -> Mapping[str, str]:
        return {"status": "ok"}

    @app.get("/capabilities")
    def capabilities() -> Mapping[str, object]:
        return {
            "adapter_id": "openai-agent-runtime/v1",
            "supported_interventions": ["REPLACE"],
            "counterfactual_profiles": [
                {"reference": item.reference, "digest": item.digest}
                for item in runtime.capabilities()
            ],
        }

    @app.post("/executions")
    def execute(request: ExecuteRequest) -> Mapping[str, object]:
        try:
            return runtime.execute(request.scenario).public_dict()
        except RuntimeInvocationError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.post("/replay")
    def replay(
        request: ReplayRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Mapping[str, object]:
        if not _is_authorized(authorization, replay_auth_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        try:
            return runtime.replay(
                request.replay_reference, request.envelope.as_runtime_envelope()
            ).public_dict()
        except RuntimeInvocationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return app


def _is_authorized(value: str | None, token: str) -> bool:
    expected = f"Bearer {token}"
    return value is not None and hmac.compare_digest(value, expected)
