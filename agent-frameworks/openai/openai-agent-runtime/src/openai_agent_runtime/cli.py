"""Commands for the independently runnable reference OpenAI agent runtime."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence

from openai_agent_runtime.contracts import Scenario
from openai_agent_runtime.core_client import GovernanceCoreClient
from openai_agent_runtime.runtime import (
    LOW_RISK_COUNTERFACTUAL,
    OpenAIAgentRuntime,
)
from openai_agent_runtime.settings import GovernanceSettings, RuntimeSettings
from openai_agent_runtime.store import FileExecutionStore


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Reference OpenAI agent runtime")
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="Start the private replay endpoint")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8091)

    run = subcommands.add_parser("run", help="Run one real observed OpenAI execution")
    run.add_argument(
        "--scenario", choices=[item.value for item in Scenario], default="aligned"
    )
    run.add_argument(
        "--ingest", action="store_true", help="Deliver bounded evidence to Core"
    )

    audit = subcommands.add_parser(
        "causal-audit", help="Run the public Core Replay → Causal Audit path"
    )
    audit.add_argument(
        "--scenario", choices=[item.value for item in Scenario], default="aligned"
    )
    audit.add_argument("--policy-id")
    audit.add_argument("--policy-version", type=int)
    audit.add_argument("--timeout-seconds", type=int, default=90)

    arguments = parser.parse_args(argv)
    if arguments.command == "serve":
        _serve(arguments.host, arguments.port)
    elif arguments.command == "run":
        _run(Scenario(arguments.scenario), arguments.ingest)
    else:
        _causal_audit(
            Scenario(arguments.scenario),
            arguments.policy_id,
            arguments.policy_version,
            arguments.timeout_seconds,
        )


def _runtime() -> tuple[OpenAIAgentRuntime, RuntimeSettings]:
    settings = RuntimeSettings.from_environment()
    return (
        OpenAIAgentRuntime.from_settings(
            settings, FileExecutionStore(settings.state_dir)
        ),
        settings,
    )


def _serve(host: str, port: int) -> None:
    import uvicorn  # type: ignore

    uvicorn.run(
        "openai_agent_runtime.api:create_app",
        factory=True,
        host=host,
        port=port,
    )


def _run(scenario: Scenario, ingest: bool) -> None:
    runtime, settings = _runtime()
    observed = runtime.execute(scenario)
    result: dict[str, object] = {"observed": observed.public_dict()}
    if ingest:
        core = GovernanceCoreClient(
            GovernanceSettings.from_environment(),
            replay_endpoint=_required_endpoint(settings),
        )
        result["core_execution_id"] = core.ingest_observed_execution(
            observed
        ).core_execution_id
    print(json.dumps(result, default=str, sort_keys=True))


def _causal_audit(
    scenario: Scenario,
    policy_id: str | None,
    policy_version: int | None,
    timeout_seconds: int,
) -> None:
    if bool(policy_id) != bool(policy_version):
        raise ValueError("--policy-id and --policy-version must be supplied together.")
    runtime, settings = _runtime()
    core = GovernanceCoreClient(
        GovernanceSettings.from_environment(),
        replay_endpoint=_required_endpoint(settings),
    )
    observed = runtime.execute(scenario)
    ingested = core.ingest_observed_execution(observed)
    if policy_id is None:
        policy_id, policy_version = core.create_and_activate_replacement_policy(
            counterfactual_reference=LOW_RISK_COUNTERFACTUAL.reference,
            counterfactual_digest=LOW_RISK_COUNTERFACTUAL.digest,
        )
    audit_id = core.submit_causal_audit(
        ingested.core_execution_id, policy_id, int(policy_version)
    )
    audit = _wait_for_audit(core, audit_id, timeout_seconds)
    print(
        json.dumps(
            {
                "core_execution_id": ingested.core_execution_id,
                "observed_decision": observed.decision.value,
                "observed_outcome_score": observed.outcome_score,
                "policy_id": policy_id,
                "policy_version": policy_version,
                "audit_id": audit_id,
                "audit_status": audit.get("status"),
                "classification": audit.get("classification"),
                "tool_call_results": audit.get("tool_call_results", []),
            },
            default=str,
            sort_keys=True,
        )
    )


def _wait_for_audit(
    core: GovernanceCoreClient, audit_id: str, timeout_seconds: int
) -> object:
    deadline = time.monotonic() + max(1, timeout_seconds)
    latest = core.get_causal_audit(audit_id)
    while latest.get("status") not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        if time.monotonic() >= deadline:
            raise TimeoutError("Causal Audit did not reach a terminal state in time.")
        time.sleep(1)
        latest = core.get_causal_audit(audit_id)
    if latest.get("status") != "SUCCEEDED":
        raise RuntimeError("Causal Audit did not complete successfully.")
    return latest


def _required_endpoint(settings: RuntimeSettings) -> str:
    if not settings.public_replay_endpoint:
        raise ValueError("OPENAI_AGENT_RUNTIME_PUBLIC_REPLAY_ENDPOINT is required.")
    return settings.public_replay_endpoint
