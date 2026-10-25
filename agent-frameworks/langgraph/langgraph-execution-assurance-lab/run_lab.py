"""Run the live AIGP banking execution-assurance scenarios."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from aigp import AIGPError, Client, Settings
from banking import BankingStore


class State(TypedDict, total=False):
    approved: bool


def _graph(action):
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(State)
    graph.add_node("governed_approval", lambda _state: {"approved": action()})
    graph.add_edge(START, "governed_approval")
    graph.add_edge("governed_approval", END)
    return graph.compile()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenario",
        choices=(
            "authorized",
            "unauthorized",
            "revoked",
            "crash",
            "recover",
            "invalid-manifest",
            "causal-audit",
        ),
    )
    parser.add_argument("--database", type=Path, default=Path("banking.db"))
    parser.add_argument("--state", type=Path, default=Path("recovery-state.json"))
    parser.add_argument(
        "--snapshot-database",
        type=Path,
        default=Path("langgraph-private-snapshots.db"),
    )
    parser.add_argument("--execution-id")
    parser.add_argument("--intervention-policy-id")
    parser.add_argument("--intervention-policy-version", type=int)
    args = parser.parse_args()
    store = BankingStore(args.database)
    store.seed()
    if args.scenario == "recover":
        _recover(store, args.state)
        return
    if args.scenario == "causal-audit":
        _start_causal_audit(args)
        return
    settings = Settings.from_environment()
    correlation_id = f"banking-assurance-{uuid4().hex}"
    client = Client(settings, correlation_id)
    execution_id = client.start_execution(
        correlation_id, invalid_manifest=args.scenario == "invalid-manifest"
    )
    arguments = {"loan_id": "LOAN-001", "requested_status": "APPROVED"}
    if args.scenario == "invalid-manifest":
        client.tool_evidence(execution_id, "manifest-probe", arguments)
        client.complete(execution_id, "FAILED")
        eligibility = client.causal_eligibility(execution_id)
        if eligibility.get("code") != "NOT_REPLAYABLE":
            raise AIGPError(f"Invalid manifest was not rejected: {eligibility}")
        _print({"scenario": "invalid-manifest", "before": store.status(), "after": store.status(), "execution_id": execution_id, "correlation_id": correlation_id, "eligibility": eligibility})
        return
    delegation = client.issue_delegation(execution_id, "LOAN-001")
    if args.scenario == "revoked":
        client.revoke(str(delegation["delegation_id"]))
    if args.scenario == "unauthorized":
        arguments["loan_id"] = "LOAN-UNAUTHORIZED"
    before = store.status()
    decision = client.authorize(execution_id, delegation, arguments)
    if decision.get("outcome") != "ALLOW":
        client.complete(execution_id, "FAILED")
        _print({"scenario": args.scenario, "before": before, "after": store.status(), "decision": decision, "execution_id": execution_id, "correlation_id": correlation_id})
        return

    mutation_id = f"{correlation_id}:loan-approve"
    # The runtime captures only its own bounded, private replay input.  This
    # static lab state is intentionally distinct from the banking database;
    # the external adapter cannot open or mutate the original business store.
    private_snapshot_state = {
        "baseline_score": 1.0,
        "counterfactual_scores": {"sha256:" + "c" * 64: 0.2},
    }

    def mutate() -> bool:
        return store.approve(mutation_id).applied

    _graph(mutate).invoke({"approved": False})
    if args.scenario == "crash":
        unknown = client.receipt(execution_id, decision, arguments, "UNKNOWN")
        client.complete(execution_id, "FAILED")
        args.state.write_text(json.dumps({"execution_id": execution_id, "correlation_id": correlation_id, "database": str(args.database), "arguments": arguments, "decision": decision, "unknown_receipt_id": unknown["event_id"], "mutation_id": mutation_id}, indent=2))
        _print({"scenario": "mutation-then-crash", "before": before, "after": store.status(), "recovery_state": str(args.state), "execution_id": execution_id, "correlation_id": correlation_id})
        return
    client.tool_evidence(execution_id, str(decision["decision_id"]), arguments)
    client.receipt(execution_id, decision, arguments, "SUCCEEDED")
    client.complete(execution_id, "SUCCEEDED")
    eligibility = client.causal_eligibility(execution_id)
    manifest_hash = eligibility.get("replay_manifest_hash")
    if not isinstance(manifest_hash, str):
        raise AIGPError("OSS did not return a replay manifest hash for the completed execution.")
    _capture_private_snapshot(
        args.snapshot_database,
        settings,
        external_id=correlation_id,
        manifest_hash=manifest_hash,
        state=private_snapshot_state,
    )
    _print({"scenario": args.scenario, "before": before, "after": store.status(), "decision": decision, "execution_id": execution_id, "correlation_id": correlation_id, "causal_audit_eligibility": eligibility, "private_snapshot_database": str(args.snapshot_database)})


def _recover(store: BankingStore, state_path: Path) -> None:
    state = json.loads(state_path.read_text())
    client = Client(Settings.from_environment(), str(state["correlation_id"]))
    # Recovery reads durable external state. It never calls approve again.
    if store.status(str(state["arguments"]["loan_id"])) != "APPROVED":
        raise AIGPError("Cannot reconcile: banking state does not show the committed approval.")
    client.receipt(str(state["execution_id"]), state["decision"], state["arguments"], "SUCCEEDED", supersedes=str(state["unknown_receipt_id"]))
    _print({"scenario": "recovery", "after": "APPROVED", "mutation_repeated": False, "execution_id": state["execution_id"], "correlation_id": state["correlation_id"]})


def _capture_private_snapshot(
    path: Path,
    settings: Settings,
    *,
    external_id: str,
    manifest_hash: str,
    state: dict[str, object],
) -> None:
    """Keep the private snapshot with the runtime, never in AIGP OSS."""
    try:
        from aigp_langgraph_replay_adapter import SQLitePrivateSnapshotStore
    except ImportError as error:
        raise AIGPError(
            "Install aigp-langgraph-replay-adapter to capture a governed replay snapshot."
        ) from error
    SQLitePrivateSnapshotStore(path).capture(
        replay_reference=f"langgraph://replays/{external_id}",
        organization_id=settings.organization_id,
        project_id=settings.project_id,
        external_execution_id=external_id,
        adapter_id="langgraph/v1",
        adapter_version="1",
        isolation_guarantee="sqlite-copy/v1",
        manifest_hash=manifest_hash,
        state=state,
    )


def _start_causal_audit(args: argparse.Namespace) -> None:
    if not args.execution_id:
        raise AIGPError("--execution-id is required for causal-audit.")
    if not args.intervention_policy_id or not args.intervention_policy_version:
        raise AIGPError(
            "--intervention-policy-id and --intervention-policy-version are required."
        )
    correlation_id = f"banking-causal-audit-{uuid4().hex}"
    response = Client(Settings.from_environment(), correlation_id).start_causal_audit(
        args.execution_id,
        args.intervention_policy_id,
        args.intervention_policy_version,
    )
    _print({
        "scenario": "causal-audit",
        "audit": response,
        "execution_id": args.execution_id,
        "correlation_id": correlation_id,
        "next_step": "Run the OSS replay worker with the LangGraph adapter installed.",
    })


def _print(value: dict[str, object]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
