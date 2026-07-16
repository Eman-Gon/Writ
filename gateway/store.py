"""Append-only decision store.

DecisionRecords are never updated in place: executed/downstream_effect arrive
as follow-up events that get_decision() folds in on read. SQLite today; the
shape (one table, JSON payload) is the Postgres JSONB shape, so migration is a
connection string, not a rewrite.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

from schemas import DecisionRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_records (
    decision_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    decided_at  TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_events (
    event_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id       TEXT NOT NULL REFERENCES decision_records(decision_id),
    executed          INTEGER NOT NULL,
    downstream_effect TEXT,
    created_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS envelope_pins (
    envelope_id   TEXT PRIMARY KEY,
    artifact_hash TEXT NOT NULL,
    first_seen    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commits (
    proposal_id       TEXT PRIMARY KEY,
    decision_id       TEXT NOT NULL,
    status            TEXT NOT NULL,
    downstream_effect TEXT,
    created_at        TEXT NOT NULL,
    finished_at       TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | None = None):
        path = path or os.environ.get("STATEGUARD_DB", "stateguard.db")
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._conn:
            self._conn.executescript(_SCHEMA)

    # -- decisions ----------------------------------------------------------

    def append_decision(self, record: DecisionRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO decision_records VALUES (?, ?, ?, ?, ?)",
                (
                    record.decision_id,
                    record.proposal_id,
                    record.run_id,
                    record.decided_at.isoformat(),
                    record.model_dump_json(),
                ),
            )

    def append_effect(self, decision_id: str, executed: bool, downstream_effect: str | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO decision_events (decision_id, executed, downstream_effect, created_at)"
                " VALUES (?, ?, ?, ?)",
                (decision_id, int(executed), downstream_effect, _now()),
            )

    def get_decision(self, decision_id: str) -> DecisionRecord | None:
        row = self._conn.execute(
            "SELECT record_json FROM decision_records WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        if row is None:
            return None
        record = DecisionRecord.model_validate_json(row[0])
        event = self._conn.execute(
            "SELECT executed, downstream_effect FROM decision_events"
            " WHERE decision_id = ? ORDER BY event_id DESC LIMIT 1",
            (decision_id,),
        ).fetchone()
        if event is not None:
            record = record.model_copy(
                update={"executed": bool(event[0]), "downstream_effect": event[1]}
            )
        return record

    # -- envelope hash pins (evidence immutability, TOCTOU) ------------------

    def pin_envelope(self, envelope_id: str, artifact_hash: str) -> bool:
        """Pin the hash on first sight; True iff it matches the pin."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT artifact_hash FROM envelope_pins WHERE envelope_id = ?", (envelope_id,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO envelope_pins VALUES (?, ?, ?)",
                    (envelope_id, artifact_hash, _now()),
                )
                return True
            return row[0] == artifact_hash

    # -- broker idempotency ledger -------------------------------------------

    def claim_commit(self, proposal_id: str, decision_id: str) -> bool:
        """Claim the idempotency key BEFORE executing. False if already claimed."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO commits (proposal_id, decision_id, status, created_at)"
                    " VALUES (?, ?, 'pending', ?)",
                    (proposal_id, decision_id, _now()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def get_commit(self, proposal_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT decision_id, status, downstream_effect FROM commits WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        return {"decision_id": row[0], "status": row[1], "downstream_effect": row[2]}

    def finish_commit(self, proposal_id: str, downstream_effect: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE commits SET status = 'done', downstream_effect = ?, finished_at = ?"
                " WHERE proposal_id = ?",
                (downstream_effect, _now(), proposal_id),
            )

    def release_commit(self, proposal_id: str) -> None:
        """Revalidation refused after the key was claimed: no business effect
        occurred, so release the key and let a corrected retry re-evaluate."""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM commits WHERE proposal_id = ? AND status = 'pending'",
                (proposal_id,),
            )


_default: Store | None = None


def get_store() -> Store:
    global _default
    if _default is None:
        _default = Store()
    return _default
