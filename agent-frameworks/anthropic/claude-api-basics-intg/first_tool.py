"""Guide 2: application-owned Messages API tool selection and execution."""

from __future__ import annotations

import hashlib
import os
import sys
import time
from typing import Any

from aigp_evidence import EvidenceReporter, Settings, new_execution_id
from policy import TOOL, lookup_policy


AGENT_ID = "claude-first-tool"
SYSTEM = """You are an insurance assistant. Use lookup_policy exactly once for the
requested policy, then give a short answer stating whether the claim can proceed."""


def _require_environment() -> str:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required.")
    model = os.getenv("CLAUDE_MODEL")
    if not model:
        raise RuntimeError("CLAUDE_MODEL is required.")
    return model


def _usage(message: object) -> dict[str, int]:
    usage = getattr(message, "usage", None)
    return {
        field: value
        for field in ("input_tokens", "output_tokens")
        if isinstance((value := getattr(usage, field, None)), int) and value >= 0
    }


def _record_model(reporter: EvidenceReporter, message: object, model: str, ordinal: int) -> None:
    reporter.event(
        "MODEL_CALL",
        actor_type="MODEL",
        actor_id=model,
        attributes={
            "ordinal": ordinal,
            "model": model,
            "status": "COMPLETED",
            "stop_reason": str(getattr(message, "stop_reason", "unknown")),
            "usage": _usage(message),
        },
        key=f"model-{ordinal}",
    )


def _text_from(message: object) -> str:
    text = [
        str(block.text)
        for block in getattr(message, "content", ())
        if getattr(block, "type", None) == "text"
    ]
    if not text:
        raise RuntimeError("Claude returned no final text content.")
    return "\n".join(text)


def _tool_use(message: object) -> object:
    calls = [
        block
        for block in getattr(message, "content", ())
        if getattr(block, "type", None) == "tool_use"
    ]
    if len(calls) != 1 or getattr(calls[0], "name", None) != TOOL["name"]:
        raise RuntimeError("Expected exactly one lookup_policy tool call.")
    return calls[0]


def run(policy_id: str = "POL-1001") -> tuple[str, str, EvidenceReporter]:
    """Let Claude choose a tool; execute it in this application, not in AIGP."""
    model = _require_environment()
    from anthropic import Anthropic

    external_execution_id = new_execution_id()
    reporter = EvidenceReporter(external_execution_id, Settings.from_environment())
    reporter.start(agent_id=AGENT_ID, agent_name="First Claude Tool", workflow="first_tool")
    user_message = f"Check whether policy {policy_id} is active and whether the claim can proceed."
    try:
        with Anthropic() as client:
            first = client.messages.create(
                model=model,
                max_tokens=256,
                system=SYSTEM,
                tools=[TOOL],
                messages=[{"role": "user", "content": user_message}],
            )
            _record_model(reporter, first, model, 1)
            tool_use = _tool_use(first)
            input_value = getattr(tool_use, "input", {})
            requested_policy_id = str(input_value.get("policy_id", "")) if isinstance(input_value, dict) else ""
            started = time.monotonic()
            result = lookup_policy(requested_policy_id)
            duration_ms = max(0, round((time.monotonic() - started) * 1000))
            tool_use_id = str(getattr(tool_use, "id", ""))
            tool_hash = hashlib.sha256(tool_use_id.encode("utf-8")).hexdigest()[:24]
            reporter.event(
                "TOOL_CALL",
                actor_type="TOOL",
                actor_id=TOOL["name"],
                attributes={
                    "ordinal": 1,
                    "provider_tool_name": TOOL["name"],
                    "status": "SUCCEEDED" if result["ok"] else "FAILED",
                    "reason_code": result["reason_code"],
                    "latency_ms": duration_ms,
                    "tool_use_id_hash": tool_hash,
                },
                key=f"tool-{tool_hash}",
                tool_call_context={
                    "schema_version": "1",
                    "runtime_tool_call_id": tool_use_id,
                    "tool_call_group_id": tool_use_id,
                    "depends_on_tool_call_ids": [],
                },
            )
            second = client.messages.create(
                model=model,
                max_tokens=256,
                system=SYSTEM,
                tools=[TOOL],
                messages=[
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": first.content},
                    {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": result["content"], "is_error": not result["ok"]}],
                    },
                ],
            )
        _record_model(reporter, second, model, 2)
        reporter.complete("SUCCEEDED")
        return _text_from(second), external_execution_id, reporter
    except Exception:
        reporter.complete("FAILED")
        raise


def main() -> None:
    policy_id = sys.argv[1] if len(sys.argv) == 2 else "POL-1001"
    try:
        answer, external_execution_id, reporter = run(policy_id)
    except (RuntimeError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(f"Policy: {policy_id}")
    print(f"Claude response: {answer}")
    print(f"Claude execution ID: {external_execution_id}")
    print(f"AIGP evidence: {reporter.status}")
    if reporter.execution_id:
        print(f"AIGP execution ID: {reporter.execution_id}")


if __name__ == "__main__":
    main()
