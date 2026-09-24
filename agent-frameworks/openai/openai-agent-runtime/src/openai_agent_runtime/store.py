"""Private, local frozen state owned solely by the reference runtime."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from openai_agent_runtime.contracts import Decision, Scenario


class RuntimeStateError(RuntimeError):
    """A frozen runtime execution is unavailable or malformed."""


@dataclass(frozen=True)
class FrozenExecution:
    external_execution_id: str
    replay_reference: str
    scenario: Scenario
    runtime_tool_call_id: str
    evidence_reference: str
    evidence_digest: str
    decision: Decision
    outcome_score: float
    evidence: dict[str, Any]

    def serialise(self) -> dict[str, Any]:
        value = asdict(self)
        value["scenario"] = self.scenario.value
        value["decision"] = self.decision.value
        return value

    @classmethod
    def deserialise(cls, value: dict[str, Any]) -> FrozenExecution:
        try:
            return cls(
                external_execution_id=str(value["external_execution_id"]),
                replay_reference=str(value["replay_reference"]),
                scenario=Scenario(str(value["scenario"])),
                runtime_tool_call_id=str(value["runtime_tool_call_id"]),
                evidence_reference=str(value["evidence_reference"]),
                evidence_digest=str(value["evidence_digest"]),
                decision=Decision(str(value["decision"])),
                outcome_score=float(value["outcome_score"]),
                evidence=dict(value["evidence"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeStateError("Frozen runtime state is malformed.") from error


class ExecutionStore(Protocol):
    def save(self, execution: FrozenExecution) -> None: ...

    def get(self, replay_reference: str) -> FrozenExecution: ...


class FileExecutionStore:
    """One JSON document per runtime execution for an independently runnable lab."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._directory.mkdir(parents=True, exist_ok=True)

    def save(self, execution: FrozenExecution) -> None:
        target = self._path(execution.replay_reference)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(execution.serialise(), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(target)

    def get(self, replay_reference: str) -> FrozenExecution:
        target = self._path(replay_reference)
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise RuntimeStateError("Unknown replay reference.") from error
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeStateError("Frozen runtime state cannot be read.") from error
        if not isinstance(value, dict):
            raise RuntimeStateError("Frozen runtime state is malformed.")
        return FrozenExecution.deserialise(value)

    def _path(self, replay_reference: str) -> Path:
        prefix = "openai-agent-runtime://replays/"
        if not replay_reference.startswith(prefix):
            raise RuntimeStateError("Unknown replay reference.")
        identifier = replay_reference.removeprefix(prefix)
        if not identifier or any(
            character not in "0123456789abcdef-" for character in identifier
        ):
            raise RuntimeStateError("Unknown replay reference.")
        return self._directory / f"{identifier}.json"


class InMemoryExecutionStore:
    """Credential-free test implementation; it deliberately retains no files."""

    def __init__(self) -> None:
        self.executions: dict[str, FrozenExecution] = {}

    def save(self, execution: FrozenExecution) -> None:
        self.executions[execution.replay_reference] = execution

    def get(self, replay_reference: str) -> FrozenExecution:
        try:
            return self.executions[replay_reference]
        except KeyError as error:
            raise RuntimeStateError("Unknown replay reference.") from error
