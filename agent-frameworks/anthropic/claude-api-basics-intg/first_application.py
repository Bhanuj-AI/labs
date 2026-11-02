"""Guide 1: a minimal Claude Messages API application with optional evidence."""

from __future__ import annotations

import os
import sys

from aigp_evidence import EvidenceReporter, Settings, new_execution_id


AGENT_ID = "claude-first-application"
PROMPT = "In one sentence, explain why a release checklist is useful."


def _require_environment() -> str:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required.")
    model = os.getenv("CLAUDE_MODEL")
    if not model:
        raise RuntimeError("CLAUDE_MODEL is required.")
    return model


def _text_from(message: object) -> str:
    blocks = getattr(message, "content", ())
    text = [str(block.text) for block in blocks if getattr(block, "type", None) == "text"]
    if not text:
        raise RuntimeError("Claude returned no text content.")
    return "\n".join(text)


def _usage(message: object) -> dict[str, int]:
    usage = getattr(message, "usage", None)
    values = {}
    for field in ("input_tokens", "output_tokens"):
        value = getattr(usage, field, None)
        if isinstance(value, int) and value >= 0:
            values[field] = value
    return values


def run() -> tuple[str, str, EvidenceReporter]:
    """Run the unchanged Claude request; AIGP never receives its text payload."""
    model = _require_environment()
    from anthropic import Anthropic

    external_execution_id = new_execution_id()
    reporter = EvidenceReporter(external_execution_id, Settings.from_environment())
    reporter.start(agent_id=AGENT_ID, agent_name="First Claude Application", workflow="first_application")
    try:
        with Anthropic() as client:
            message = client.messages.create(
                model=model,
                max_tokens=128,
                messages=[{"role": "user", "content": PROMPT}],
            )
        reporter.event(
            "MODEL_CALL",
            actor_type="MODEL",
            actor_id=model,
            attributes={
                "ordinal": 1,
                "model": model,
                "status": "COMPLETED",
                "stop_reason": str(getattr(message, "stop_reason", "unknown")),
                "usage": _usage(message),
            },
            key="model-1",
        )
        reporter.complete("SUCCEEDED")
        return _text_from(message), external_execution_id, reporter
    except Exception:
        reporter.complete("FAILED")
        raise


def main() -> None:
    try:
        answer, external_execution_id, reporter = run()
    except (RuntimeError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(f"Claude response: {answer}")
    print(f"Claude execution ID: {external_execution_id}")
    print(f"AIGP evidence: {reporter.status}")
    if reporter.execution_id:
        print(f"AIGP execution ID: {reporter.execution_id}")


if __name__ == "__main__":
    main()
