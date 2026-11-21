"""Run the LangGraph ToolNode v2 tutorial against a local Docker platform.

The runner creates a fresh observed execution and active opaque-reference
policy, stores synthetic raw evidence only in a runtime-owned snapshot, and
waits for the replay worker to finish one controlled Causal Audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

from aigp import AIGPError, Client, Settings
from bhanuj_governance_plugin_api import (
    ExecutionReplayCapability,
    ToolArgumentDigestAlgorithm,
    create_tool_argument_digest,
)

from aigp_langgraph_replay_adapter import SQLitePrivateSnapshotStore

ROOT = Path(__file__).resolve().parent
RUNTIME_DATA = ROOT / "runtime-data"
ARTIFACTS = ROOT / "artifacts"
SNAPSHOT_PATH = RUNTIME_DATA / "langgraph-v2-snapshots.db"
EVIDENCE_SCHEMA_ID = "langgraph-tool-node-account-risk-v2"
EVIDENCE_SCHEMA_VERSION = "v1"
REPLACEMENT_REFERENCE = "private://langgraph-v2/approved-account-risk-low"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenario", choices=("observe", "audit", "full"), default="full", nargs="?"
    )
    parser.add_argument("--execution-id", help="Observed execution for the audit scenario.")
    parser.add_argument("--policy-id", help="Active policy for the audit scenario.")
    parser.add_argument("--policy-version", type=int, help="Active policy version.")
    parser.add_argument("--timeout-seconds", type=int, default=90)
    arguments = parser.parse_args()

    settings = Settings.from_environment()
    if arguments.scenario == "observe":
        result = observe(settings)
    elif arguments.scenario == "audit":
        if not all(
            (arguments.execution_id, arguments.policy_id, arguments.policy_version)
        ):
            parser.error(
                "audit requires --execution-id, --policy-id, and --policy-version."
            )
        result = audit(
            settings,
            execution_id=arguments.execution_id,
            policy_id=arguments.policy_id,
            policy_version=arguments.policy_version,
            timeout_seconds=arguments.timeout_seconds,
        )
    else:
        observed = observe(settings)
        audit_result = audit(
            settings,
            execution_id=str(observed["execution_id"]),
            policy_id=str(observed["policy_id"]),
            policy_version=int(observed["policy_version"]),
            timeout_seconds=arguments.timeout_seconds,
        )
        result = {"observed": observed, "audit": audit_result}
    path = _write_artifact(result)
    print(json.dumps({**result, "capture_artifact": str(path)}, indent=2, sort_keys=True))


def observe(settings: Settings) -> dict[str, object]:
    """Record one ToolNode call and its private snapshot without running a replay."""

    correlation_id = f"langgraph-v2-live-{uuid4().hex}"
    external_execution_id = f"langgraph-v2-execution-{uuid4().hex}"
    runtime_tool_call_id = f"{correlation_id}:account-lookup"
    replay_reference = f"runtime-private://langgraph-v2/{external_execution_id}"
    evidence_reference = f"private://langgraph-v2/{external_execution_id}/recorded"
    replacement_reference = REPLACEMENT_REFERENCE
    tool_arguments: dict[str, object] = {"account_id": "A-100"}
    recorded_evidence = _canonical_json(
        {"account_id": "A-100", "risk": "medium", "source": "recorded"}
    )
    replacement_evidence = _canonical_json(
        {"account_id": "A-100", "risk": "low", "source": "approved-replacement"}
    )
    recorded_digest = _response_digest(recorded_evidence)
    replacement_digest = _response_digest(replacement_evidence)
    capability = _replay_capability(replay_reference)
    client = Client(settings, correlation_id)
    execution_id = client.start_execution(external_execution_id, capability)
    tool_identity = {
        "scope_id": "graph.root",
        "tool_symbol": "account_lookup",
        "tool_schema_version": "v1",
        "argument_digest": create_tool_argument_digest(
            tool_arguments,
            algorithm=ToolArgumentDigestAlgorithm.SHA256_JSON_V1,
            organization_id=settings.organization_id,
            project_id=settings.project_id,
        ).to_payload(),
        "occurrence": 1,
    }
    event_id = client.add_tool_evidence(
        execution_id,
        runtime_tool_call_id=runtime_tool_call_id,
        evidence_reference=evidence_reference,
        evidence_digest=recorded_digest,
        tool_identity=tool_identity,
        schema_id=EVIDENCE_SCHEMA_ID,
        schema_version=EVIDENCE_SCHEMA_VERSION,
    )
    client.add_baseline_score(execution_id)
    client.complete(execution_id)
    policy = client.active_policy(
        replacement_reference=replacement_reference,
        replacement_digest=replacement_digest,
        schema_id=EVIDENCE_SCHEMA_ID,
        schema_version=EVIDENCE_SCHEMA_VERSION,
    ) or client.create_policy(
        replacement_reference=replacement_reference,
        replacement_digest=replacement_digest,
        schema_id=EVIDENCE_SCHEMA_ID,
        schema_version=EVIDENCE_SCHEMA_VERSION,
    )
    policy_id = _required_string(policy, "policy_id")
    policy_version = _required_int(policy, "version")
    if policy.get("status") != "ACTIVE":
        client.validate_and_activate_policy(policy_id, policy_version)
    eligibility = client.eligibility(
        execution_id, policy_id=policy_id, policy_version=policy_version
    )
    manifest_hash = eligibility.get("replay_manifest_hash")
    if eligibility.get("code") != "ELIGIBLE" or not isinstance(manifest_hash, str):
        raise AIGPError(
            "The observed execution was not eligible for a Causal Audit: "
            f"{eligibility.get('reason', 'unknown reason')}"
        )

    _capture_private_snapshot(
        settings,
        replay_reference=replay_reference,
        external_execution_id=external_execution_id,
        manifest_hash=manifest_hash,
        event_id=event_id,
        runtime_tool_call_id=runtime_tool_call_id,
        evidence_reference=evidence_reference,
        recorded_evidence=recorded_evidence,
        recorded_digest=recorded_digest,
        replacement_reference=replacement_reference,
        replacement_evidence=replacement_evidence,
        replacement_digest=replacement_digest,
        tool_arguments=tool_arguments,
    )
    return {
        "scenario": "observe",
        "execution_id": execution_id,
        "tool_event_id": event_id,
        "runtime_tool_call_id": runtime_tool_call_id,
        "replay_manifest_hash": manifest_hash,
        "policy_id": policy_id,
        "policy_version": policy_version,
        "resolution_plan_input": "one declared account_lookup call",
        "intervention_policy": {
            "policy_id": policy_id,
            "policy_version": policy_version,
            "provider": "opaque-reference/v1",
        },
        "private_snapshot": {
            "location": str(SNAPSHOT_PATH),
            "mounted_read_only_to": "ai-governance-replay-worker",
            "integrity": "manifest, tenant, adapter, and digest bound",
        },
        "studio": {
            "execution": f"http://localhost:3000/agents-runtime/executions/{execution_id}",
            "causal_audit": "http://localhost:3000/agents-runtime?view=causal-audit",
        },
    }


def audit(
    settings: Settings,
    *,
    execution_id: str,
    policy_id: str,
    policy_version: int,
    timeout_seconds: int,
) -> dict[str, object]:
    """Start an audit and wait for the replay worker's terminal evidence."""

    client = Client(settings, f"langgraph-v2-audit-{uuid4().hex}")
    response = client.start_causal_audit(execution_id, policy_id, policy_version)
    audit_id = _required_string(response, "audit_id")
    deadline = time.monotonic() + timeout_seconds
    while True:
        result = client.causal_audit(audit_id)
        status = result.get("status")
        if status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        if time.monotonic() >= deadline:
            raise AIGPError(
                f"Causal Audit {audit_id} did not reach a terminal state within "
                f"{timeout_seconds} seconds."
            )
        time.sleep(1)
    return {
        "scenario": "audit",
        "audit_id": audit_id,
        "execution_id": execution_id,
        "status": status,
        "classification": result.get("classification"),
        "failure_code": result.get("failure_code"),
        "replays": _replay_summary(result.get("tool_call_results")),
        "studio": {
            "causal_audit": f"http://localhost:3000/agents-runtime/causal-audits/{audit_id}",
            "replays": "http://localhost:3000/replays",
        },
    }


def _replay_capability(replay_reference: str) -> dict[str, object]:
    """Return the Phase 1 declaration plus the opt-in public Phase 2 payload."""

    execution_capability = ExecutionReplayCapability(
        supported_tool_boundaries=("langgraph.tool-node",),
        unsupported_execution_surfaces=(
            "langgraph.custom-node",
            "direct-sdk-call",
            "provider-hosted-tool",
        ),
        argument_digest_algorithms=(ToolArgumentDigestAlgorithm.SHA256_JSON_V1,),
        max_trace_size=8,
        supports_async_tools=True,
        supports_subgraphs=False,
        supports_retries=True,
        supports_concurrent_execution=True,
        evidence_only=True,
        no_original_tool_fallback=True,
    )
    return {
        "runtime_type": "langgraph",
        "adapter_id": "langgraph/v2",
        "adapter_version": "2",
        "replay_reference": replay_reference,
        "isolation_guarantee": "runtime-private-evidence/v1",
        "supported_interventions": ["REPLACE"],
        "evidence_only_replay_capability": {
            "schema_version": "evidence-only-replay-capability/v1",
            "recorded_evidence_only": True,
            "unobserved_tool_calls_fail": True,
            "external_side_effects_prohibited": True,
        },
        "metadata": {"execution_replay_capability": execution_capability.to_payload()},
    }


def _capture_private_snapshot(
    settings: Settings,
    *,
    replay_reference: str,
    external_execution_id: str,
    manifest_hash: str,
    event_id: str,
    runtime_tool_call_id: str,
    evidence_reference: str,
    recorded_evidence: str,
    recorded_digest: str,
    replacement_reference: str,
    replacement_evidence: str,
    replacement_digest: str,
    tool_arguments: dict[str, object],
) -> None:
    """Persist private material locally; AIGP OSS never receives this payload."""

    state = {
        "tool_node_replay": {
            "schema_version": "langgraph-tool-node-replay-snapshot/v1",
            "tool_calls": [
                {
                    "planned_call_id": event_id,
                    "runtime_tool_call_id": runtime_tool_call_id,
                    "tool_symbol": "account_lookup",
                    "scope_id": "graph.root",
                    "tool_schema_version": "v1",
                    "occurrence": 1,
                    "dependency_call_ids": [],
                    "arguments": tool_arguments,
                }
            ],
            "evidence": {
                evidence_reference: {
                    "content": recorded_evidence,
                    "response_digest": recorded_digest,
                },
                replacement_reference: {
                    "content": replacement_evidence,
                    "response_digest": replacement_digest,
                },
            },
            "intervention_outcome_score": 0.2,
        }
    }
    SQLitePrivateSnapshotStore(SNAPSHOT_PATH).capture(
        replay_reference=replay_reference,
        organization_id=settings.organization_id,
        project_id=settings.project_id,
        external_execution_id=external_execution_id,
        adapter_id="langgraph/v2",
        adapter_version="2",
        isolation_guarantee="runtime-private-evidence/v1",
        manifest_hash=manifest_hash,
        state=state,
    )


def _response_digest(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _replay_summary(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    summary: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        summary.append(
            {
                "tool_call_id": item.get("tool_call_id"),
                "counterfactual_replay_ids": item.get("counterfactual_replay_ids", []),
                "counterfactual_execution_ids": item.get(
                    "counterfactual_execution_ids", []
                ),
                "influence_score": item.get("influence_score"),
            }
        )
    return summary


def _write_artifact(result: dict[str, object]) -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / f"live-{int(time.time())}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return path


def _required_string(value: dict[str, object], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result:
        raise AIGPError(f"AIGP response lacked {name}.")
    return result


def _required_int(value: dict[str, object], name: str) -> int:
    result = value.get(name)
    if isinstance(result, bool) or not isinstance(result, int):
        raise AIGPError(f"AIGP response lacked integer {name}.")
    return result


if __name__ == "__main__":
    main()
