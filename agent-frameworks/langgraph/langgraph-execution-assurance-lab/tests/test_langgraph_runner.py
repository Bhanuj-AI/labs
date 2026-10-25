import pytest


def test_graph_runs_a_governed_node() -> None:
    pytest.importorskip("langgraph")
    from run_lab import _graph

    assert _graph(lambda: True).invoke({"approved": False})["approved"] is True
