from pathlib import Path

import pytest
from aigp import Settings
from run_lab import _capture_private_snapshot


def test_private_snapshot_is_manifest_and_tenant_bound(tmp_path: Path) -> None:
    adapter = pytest.importorskip("aigp_langgraph_replay_adapter")
    plugin_api = pytest.importorskip("bhanuj_governance_plugin_api")
    from aigp_langgraph_replay_adapter.snapshot_store import PrivateSnapshotError

    manifest_hash = "a" * 64
    settings = Settings(
        "http://localhost:8000",
        "org-lab",
        "project-lab",
        "runtime-lab",
        "issuer-lab",
        "issuer-token",
    )
    path = tmp_path / "private-snapshots.db"

    _capture_private_snapshot(
        path,
        settings,
        external_id="execution-lab",
        manifest_hash=manifest_hash,
        state={"baseline_score": 1.0, "counterfactual_scores": {}},
    )

    source = plugin_api.RuntimeReplaySource(
        source_agent_execution_id="agent-execution-lab",
        external_execution_id="execution-lab",
        runtime_type="langgraph",
        adapter_id="langgraph/v1",
        adapter_version="1",
        replay_reference="langgraph://replays/execution-lab",
        isolation_guarantee="sqlite-copy/v1",
        manifest_hash=manifest_hash,
    )
    resolved = adapter.SQLitePrivateSnapshotStore(path).resolve(
        source, "org-lab", "project-lab"
    )
    assert resolved.state["baseline_score"] == 1.0

    forged = plugin_api.RuntimeReplaySource(
        source_agent_execution_id="agent-execution-lab",
        external_execution_id="execution-lab",
        runtime_type="langgraph",
        adapter_id="langgraph/v1",
        adapter_version="1",
        replay_reference="langgraph://replays/execution-lab",
        isolation_guarantee="sqlite-copy/v1",
        manifest_hash="b" * 64,
    )
    with pytest.raises(PrivateSnapshotError):
        adapter.SQLitePrivateSnapshotStore(path).resolve(forged, "org-lab", "project-lab")
