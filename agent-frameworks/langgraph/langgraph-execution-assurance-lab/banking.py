"""External banking state owned by this lab, never by AIGP."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MutationResult:
    loan_id: str
    mutation_id: str
    previous_status: str
    status: str
    applied: bool


class BankingStore:
    """Idempotent SQLite mutation boundary used by the LangGraph tool."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def seed(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS loan_applications (loan_id TEXT PRIMARY KEY, status TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS mutation_log (mutation_id TEXT PRIMARY KEY, loan_id TEXT NOT NULL, status TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO loan_applications VALUES ('LOAN-001', 'PENDING')"
            )

    def status(self, loan_id: str = "LOAN-001") -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM loan_applications WHERE loan_id = ?", (loan_id,)
            ).fetchone()
        if row is None:
            raise KeyError(loan_id)
        return str(row[0])

    def approve(self, mutation_id: str, loan_id: str = "LOAN-001") -> MutationResult:
        """Apply at most once; recovery reads this durable business evidence."""
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT status FROM mutation_log WHERE mutation_id = ?", (mutation_id,)
            ).fetchone()
            current = self.status(loan_id)
            if existing is not None:
                return MutationResult(loan_id, mutation_id, current, str(existing[0]), False)
            connection.execute(
                "UPDATE loan_applications SET status = 'APPROVED' WHERE loan_id = ?", (loan_id,)
            )
            connection.execute(
                "INSERT INTO mutation_log VALUES (?, ?, 'APPROVED')", (mutation_id, loan_id)
            )
        return MutationResult(loan_id, mutation_id, current, "APPROVED", True)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
