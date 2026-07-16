"""Append-only decision store.

DecisionRecords are never updated in place: executed/downstream_effect arrive
as follow-up events that get_decision() folds in on read. SQLite today; the
shape (one table, JSON payload) is the Postgres JSONB shape, so migration is a
connection string, not a rewrite.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

from schemas import DecisionRecord, SourceEnvelope

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
CREATE TABLE IF NOT EXISTS documents (
    document_id      TEXT PRIMARY KEY,
    envelope_id      TEXT NOT NULL,
    root_document_id TEXT NOT NULL,
    queue            TEXT NOT NULL,
    sender_display   TEXT NOT NULL,
    artifact_type    TEXT NOT NULL,
    received_at      TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS envelopes (
    envelope_id   TEXT PRIMARY KEY,
    envelope_json TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    task_ref     TEXT NOT NULL,
    token_sha256 TEXT NOT NULL UNIQUE,
    expires_at   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_documents (
    task_id     TEXT NOT NULL,
    document_id TEXT NOT NULL,
    PRIMARY KEY (task_id, document_id)
);
CREATE TABLE IF NOT EXISTS task_manifest (
    task_id     TEXT NOT NULL,
    envelope_id TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (task_id, envelope_id)
);
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL
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

    # -- documents & envelopes (Writ ingestion surface) -----------------------

    def add_document(
        self,
        document_id: str,
        envelope: SourceEnvelope,
        *,
        root_document_id: str,
        queue: str,
        sender_display: str,
        received_at: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    envelope.envelope_id,
                    root_document_id,
                    queue,
                    sender_display,
                    envelope.artifact_type.value,
                    received_at,
                    _now(),
                ),
            )
            # Content-addressed envelope ids: identical bytes yield the same
            # id, so keep the first envelope rather than rewrite lineage.
            self._conn.execute(
                "INSERT OR IGNORE INTO envelopes VALUES (?, ?, ?)",
                (envelope.envelope_id, envelope.model_dump_json(), _now()),
            )

    def get_document(self, document_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT document_id, envelope_id, root_document_id, queue, sender_display,"
            " artifact_type, received_at FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "document_id": row[0],
            "envelope_id": row[1],
            "root_document_id": row[2],
            "queue": row[3],
            "sender_display": row[4],
            "artifact_type": row[5],
            "received_at": row[6],
        }

    def list_documents(self, root_document_ids: list[str], queue: str | None = None) -> list[dict]:
        if not root_document_ids:
            return []
        placeholders = ",".join("?" for _ in root_document_ids)
        sql = (
            "SELECT document_id FROM documents"
            f" WHERE root_document_id IN ({placeholders})"
        )
        params: list[str] = list(root_document_ids)
        if queue:
            sql += " AND queue = ?"
            params.append(queue)
        sql += " ORDER BY received_at, document_id"
        rows = self._conn.execute(sql, params).fetchall()
        return [self.get_document(row[0]) for row in rows]

    def get_envelope(self, envelope_id: str) -> SourceEnvelope | None:
        row = self._conn.execute(
            "SELECT envelope_json FROM envelopes WHERE envelope_id = ?", (envelope_id,)
        ).fetchone()
        return SourceEnvelope.model_validate_json(row[0]) if row else None

    # -- tasks & task-scoped manifests ----------------------------------------

    def create_task(
        self, task_id: str, task_ref: str, token_sha256: str, expires_at: str, document_ids: list[str]
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
                (task_id, task_ref, token_sha256, expires_at, _now()),
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO task_documents VALUES (?, ?)",
                [(task_id, document_id) for document_id in document_ids],
            )

    def get_task_by_token_hash(self, token_sha256: str) -> dict | None:
        row = self._conn.execute(
            "SELECT task_id, task_ref, expires_at FROM tasks WHERE token_sha256 = ?",
            (token_sha256,),
        ).fetchone()
        if row is None:
            return None
        documents = [
            r[0]
            for r in self._conn.execute(
                "SELECT document_id FROM task_documents WHERE task_id = ?", (row[0],)
            ).fetchall()
        ]
        return {"task_id": row[0], "task_ref": row[1], "expires_at": row[2], "document_ids": documents}

    def record_fetch(self, task_id: str, envelope_id: str) -> None:
        """The manifest entry. Durable BEFORE the fetch returns any text: a
        fetch that is not recorded is a laundering hole."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO task_manifest VALUES (?, ?, ?)",
                (task_id, envelope_id, _now()),
            )

    def task_manifest_envelope_ids(self, task_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT envelope_id FROM task_manifest WHERE task_id = ? ORDER BY fetched_at, envelope_id",
            (task_id,),
        ).fetchall()
        return [row[0] for row in rows]

    # -- proposals (writ_check_status scoping) ---------------------------------

    def record_proposal(self, proposal_id: str, task_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO proposals VALUES (?, ?, ?)",
                (proposal_id, task_id, _now()),
            )

    def get_proposal_task(self, proposal_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT task_id FROM proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return row[0] if row else None

    def proposal_status(self, proposal_id: str) -> tuple[str, str | None] | None:
        """(decision, effect) primitives for the agent-facing status poll.
        Deliberately NOT the DecisionRecord: the MCP layer never holds one."""
        row = self._conn.execute(
            "SELECT record_json FROM decision_records WHERE proposal_id = ?"
            " ORDER BY decided_at DESC, decision_id DESC LIMIT 1",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        decision = json.loads(row[0])["decision"]
        commit = self.get_commit(proposal_id)
        effect = commit["downstream_effect"] if commit and commit["status"] == "done" else None
        return decision, effect

    def latest_decision_for_proposal(self, proposal_id: str) -> DecisionRecord | None:
        row = self._conn.execute(
            "SELECT decision_id FROM decision_records WHERE proposal_id = ?"
            " ORDER BY decided_at DESC, decision_id DESC LIMIT 1",
            (proposal_id,),
        ).fetchone()
        return self.get_decision(row[0]) if row else None


_default: Store | None = None


def get_store() -> Store:
    global _default
    if _default is None:
        _default = Store()
    return _default
