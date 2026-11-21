"""Run the successful v2 evidence-replay tutorial paths without an OSS server."""

from __future__ import annotations

from replay_examples import EvidenceCall, build_session


def _show(title: str, messages: list[object], summary: object) -> None:
    print(f"\n{title}")
    print(f"  ToolNode response: {[getattr(message, 'content', None) for message in messages]}")
    print(f"  Resolution completion: {getattr(summary, 'completion', None)}")
    print(f"  Consumed planned calls: {getattr(summary, 'consumed_planned_call_ids', None)}")


def main() -> None:
    observed = EvidenceCall(
        planned_call_id="planned-account",
        runtime_call_id="native-account",
        tool_symbol="account_lookup",
        arguments={"account_id": "A-1"},
        recorded_content="Recorded account evidence: risk=medium",
    )
    recorded_session, _ = build_session((observed,))
    recorded_messages = recorded_session.invoke((observed.native_tool_call(),))
    _show(
        "1. Recorded evidence (the original tool is not supplied to ToolNode)",
        recorded_messages,
        recorded_session.finalize(),
    )

    intervention_session, _ = build_session(
        (observed,),
        intervention_target=observed.planned_call_id,
        replacement_content="Approved replacement evidence: risk=low",
    )
    intervention_messages = intervention_session.invoke((observed.native_tool_call(),))
    _show(
        "2. Approved replacement evidence",
        intervention_messages,
        intervention_session.finalize(),
    )


if __name__ == "__main__":
    main()
