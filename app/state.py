"""
State Management + State Machine.

Every request is a record that moves between well-defined statuses.
ALLOWED_TRANSITIONS is the "map": a request can only move along these arrows.
Any other move (for example AWAITING_APPROVAL -> EXECUTED) raises an error.

Storage: SQLite (a single local file, no server needed).
"""
from __future__ import annotations

import json
import sqlite3
from enum import Enum
from pathlib import Path

from .audit import now_iso


class Status(str, Enum):
    RECEIVED = "RECEIVED"
    UNDERSTOOD = "UNDERSTOOD"
    RETRIEVED = "RETRIEVED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTED = "EXECUTED"
    COMPLETED = "COMPLETED"          # executed + verified
    # terminal / stop states
    INVALID = "INVALID"
    NEEDS_INFO = "NEEDS_INFO"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"
    FAILED_SAFE = "FAILED_SAFE"      # a failure stopped the flow; nothing unsafe happened
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


S = Status
ALLOWED_TRANSITIONS: dict[Status, set[Status]] = {
    S.RECEIVED: {S.UNDERSTOOD, S.FAILED_SAFE},
    S.UNDERSTOOD: {S.RETRIEVED, S.BLOCKED, S.FAILED_SAFE},
    S.RETRIEVED: {S.AWAITING_APPROVAL, S.NEEDS_INFO, S.BLOCKED, S.FAILED_SAFE},
    S.AWAITING_APPROVAL: {S.APPROVED, S.REJECTED},
    S.APPROVED: {S.EXECUTED, S.FAILED_SAFE, S.BLOCKED},     # BLOCKED: rules re-checked before execution
    S.FAILED_SAFE: {S.EXECUTED, S.FAILED_SAFE, S.BLOCKED},  # an approved request may be retried later
    S.EXECUTED: {S.COMPLETED, S.VERIFICATION_FAILED},
    # terminal states: no way out
    S.COMPLETED: set(), S.NEEDS_INFO: set(), S.BLOCKED: set(),
    S.REJECTED: set(), S.VERIFICATION_FAILED: set(), S.INVALID: set(),
}


class IllegalTransition(Exception):
    pass


class StateStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS requests (
                   request_id TEXT PRIMARY KEY,
                   status     TEXT NOT NULL,
                   data       TEXT NOT NULL,
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL)"""
        )
        # Idempotency: the PRIMARY KEY guarantees one row per action, ever.
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS executed_actions (
                   idempotency_key TEXT PRIMARY KEY,
                   request_id      TEXT NOT NULL,
                   result          TEXT NOT NULL,
                   executed_at     TEXT NOT NULL)"""
        )
        self.conn.commit()

    # ---- requests ----
    def get(self, request_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT data FROM requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def create(self, state: dict) -> None:
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
            (state["request_id"], state["status"], json.dumps(state), ts, ts),
        )
        self.conn.commit()

    def save(self, state: dict) -> None:
        self.conn.execute(
            "UPDATE requests SET status = ?, data = ?, updated_at = ? WHERE request_id = ?",
            (state["status"], json.dumps(state), now_iso(), state["request_id"]),
        )
        self.conn.commit()

    def all(self) -> list[dict]:
        rows = self.conn.execute("SELECT data FROM requests ORDER BY created_at").fetchall()
        return [json.loads(r[0]) for r in rows]

    # ---- idempotency ----
    def get_action(self, key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT result FROM executed_actions WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def record_action(self, key: str, request_id: str, result: dict) -> None:
        self.conn.execute(
            "INSERT INTO executed_actions VALUES (?, ?, ?, ?)",
            (key, request_id, json.dumps(result), now_iso()),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


def transition(state: dict, new: Status, note: str = "") -> None:
    """Move a request to a new status - only if the map allows it."""
    current = Status(state["status"])
    if new not in ALLOWED_TRANSITIONS[current]:
        raise IllegalTransition(f"{current.value} -> {new.value} is not allowed")
    state["status"] = new.value
    state.setdefault("history", []).append({"ts": now_iso(), "status": new.value, "note": note})
