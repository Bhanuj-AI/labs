"""Local operational-record storage and request contracts for the LangGraph lab."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class OperationIntent(str, Enum):
    """The two deliberately small operation classes demonstrated by the lab."""

    READ = "READ"
    WRITE = "WRITE"


@dataclass(frozen=True)
class OperationRequest:
    """Bounded facts for one LangGraph invocation.

    The request contains identifiers and a proposed operational status only.  It
    deliberately has no prompt, model output, credentials, or raw database row.

    ``external_execution_id`` is the actual LangGraph invocation/run identity
    submitted to AIGP.  It is retained under its public REST name for
    compatibility; use :attr:`langgraph_run_id` inside the adapter. A thread is
    continuity context only and never selects an AIGP execution.
    """

    external_execution_id: str
    correlation_id: str
    actor_id: str
    organization_id: str
    project_id: str
    intent: OperationIntent
    record_id: str = "OPS-001"
    requested_status: str = "MAINTENANCE"
    interrupt_after_read: bool = False
    langgraph_thread_id: str | None = None
    governance_execution_id: str | None = None

    @property
    def langgraph_run_id(self) -> str:
        """Return the runtime invocation identity registered with AIGP."""

        return self.external_execution_id


@dataclass(frozen=True)
class RecordChange:
    """The bounded result of a successful local database update."""

    record_id: str
    previous_status: str
    new_status: str


class OperationsRepository:
    """A tiny SQLite repository owned by the example application."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    @property
    def database_path(self) -> Path:
        return self._database_path

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operational_records (
                    record_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO operational_records(record_id, status, summary)
                VALUES (?, ?, ?)
                """,
                ("OPS-001", "PENDING", "Scheduled maintenance record"),
            )

    def read(self, record_id: str) -> dict[str, str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_id, status, summary FROM operational_records WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Operational record {record_id!r} does not exist.")
        return {"record_id": row[0], "status": row[1], "summary": row[2]}

    def update_status(self, record_id: str, requested_status: str) -> RecordChange:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM operational_records WHERE record_id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Operational record {record_id!r} does not exist.")
            previous_status = str(row[0])
            connection.execute(
                "UPDATE operational_records SET status = ? WHERE record_id = ?",
                (requested_status, record_id),
            )
        return RecordChange(record_id, previous_status, requested_status)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
