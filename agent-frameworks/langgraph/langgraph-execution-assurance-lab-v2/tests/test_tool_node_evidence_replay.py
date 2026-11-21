from __future__ import annotations

import asyncio

import pytest
from bhanuj_governance_plugin_api import ReplayResolutionCompletion
from langgraph.prebuilt import ToolNode

from aigp_langgraph_replay_adapter import LangGraphToolNodeReplayError
from replay_examples import EvidenceCall, build_session


def _account_call() -> EvidenceCall:
    return EvidenceCall(
        planned_call_id="planned-account",
        runtime_call_id="native-account",
        tool_symbol="account_lookup",
        arguments={"account_id": "A-1"},
        recorded_content="Recorded account evidence: risk=medium",
    )


def test_tool_node_returns_recorded_evidence_without_original_tool_execution() -> None:
    original_tool_calls = 0

    def original_account_lookup(_account_id: str) -> str:
        nonlocal original_tool_calls
        original_tool_calls += 1
        return "This original tool must never run during evidence replay."

    call = _account_call()
    session, _ = build_session((call,))

    messages = session.invoke((call.native_tool_call(),))

    assert isinstance(session.tool_node, ToolNode)
    assert all(
        tool.func is not original_account_lookup
        for tool in session.tool_node.tools_by_name.values()
    )
    assert [message.content for message in messages] == [call.recorded_content]
    # The v2 session creates resolver-backed tools only; this historical function
    # is deliberately not given to ToolNode.
    assert original_tool_calls == 0
    summary = session.finalize()
    assert summary.completion is ReplayResolutionCompletion.COMPLETE
    assert summary.consumed_planned_call_ids == (call.planned_call_id,)


def test_tool_node_returns_only_approved_replacement_evidence() -> None:
    call = _account_call()
    session, _ = build_session(
        (call,),
        intervention_target=call.planned_call_id,
        replacement_content="Approved replacement evidence: risk=low",
    )

    messages = session.invoke((call.native_tool_call(),))

    assert [message.content for message in messages] == [
        "Approved replacement evidence: risk=low"
    ]
    summary = session.finalize()
    assert summary.completion is ReplayResolutionCompletion.COMPLETE
    assert summary.intervention_applied_call_id == call.planned_call_id


@pytest.mark.parametrize(
    ("call", "expected_completion"),
    (
        (
            {
                "id": "native-unknown",
                "name": "unplanned_tool",
                "args": {"account_id": "A-1"},
                "type": "tool_call",
            },
            ReplayResolutionCompletion.INCOMPLETE,
        ),
        (
            {
                "id": "native-account",
                "name": "account_lookup",
                "args": {"account_id": "forged"},
                "type": "tool_call",
            },
            ReplayResolutionCompletion.FAILED,
        ),
    ),
)
def test_unknown_or_mismatched_calls_fail_closed(
    call: dict[str, object], expected_completion: ReplayResolutionCompletion
) -> None:
    session, _ = build_session((_account_call(),))

    with pytest.raises(LangGraphToolNodeReplayError):
        session.invoke((call,))

    assert session.finalize().completion is expected_completion


def test_async_tool_node_resolves_independent_calls_in_parallel() -> None:
    account = _account_call()
    risk = EvidenceCall(
        planned_call_id="planned-risk",
        runtime_call_id="native-risk",
        tool_symbol="risk_lookup",
        arguments={"account_id": "A-1"},
        recorded_content="Recorded risk evidence: score=0.72",
    )
    session, _ = build_session((account, risk))

    messages = asyncio.run(
        session.ainvoke((account.native_tool_call(), risk.native_tool_call()))
    )

    assert {message.content for message in messages} == {
        account.recorded_content,
        risk.recorded_content,
    }
    summary = session.finalize()
    assert summary.completion is ReplayResolutionCompletion.COMPLETE
    assert summary.consumed_planned_call_ids == (
        account.planned_call_id,
        risk.planned_call_id,
    )


def test_concurrent_duplicate_calls_cannot_consume_one_planned_call_twice() -> None:
    call = _account_call()
    session, _ = build_session((call,))

    async def resolve_twice() -> list[object]:
        return list(
            await asyncio.gather(
                session.ainvoke((call.native_tool_call(),)),
                session.ainvoke((call.native_tool_call(),)),
                return_exceptions=True,
            )
        )

    results = asyncio.run(resolve_twice())

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, LangGraphToolNodeReplayError) for result in results) == 1
    summary = session.finalize()
    assert summary.consumed_planned_call_ids == (call.planned_call_id,)


def test_incomplete_session_is_visible_at_finalization() -> None:
    account = _account_call()
    risk = EvidenceCall(
        planned_call_id="planned-risk",
        runtime_call_id="native-risk",
        tool_symbol="risk_lookup",
        arguments={"account_id": "A-1"},
        recorded_content="Recorded risk evidence: score=0.72",
    )
    session, _ = build_session((account, risk))

    session.invoke((account.native_tool_call(),))

    summary = session.finalize()
    assert summary.completion is ReplayResolutionCompletion.INCOMPLETE
    assert summary.remaining_planned_call_ids == (risk.planned_call_id,)
