"""Task-scoped sessions for the MCP surface.

A session is a resolved, unexpired task token. Everything the agent may do is
bounded by it: which documents it can fetch (the orchestrator's allowlist) and
which manifest its fetches accumulate into. The manifest entry is written
durably BEFORE any text is returned — a fetch that is not recorded is a
laundering hole, so durability comes first and the return path second.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from schemas import ContextManifest, SourceEnvelope

from api.tasks import hash_token
from gateway.store import Store


@dataclass(frozen=True)
class TaskSession:
    store: Store
    task_id: str
    task_ref: str
    document_ids: tuple[str, ...]
    expires_at: datetime


def resolve_session(store: Store, task_token: str) -> TaskSession | None:
    """Token -> session, or None. Unknown, malformed, and expired tokens are
    indistinguishable to the caller; all fail closed."""
    if not task_token:
        return None
    task = store.get_task_by_token_hash(hash_token(task_token))
    if task is None:
        return None
    try:
        expires_at = datetime.fromisoformat(task["expires_at"])
    except ValueError:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expires_at:
        return None
    return TaskSession(
        store=store,
        task_id=task["task_id"],
        task_ref=task["task_ref"],
        document_ids=tuple(task["document_ids"]),
        expires_at=expires_at,
    )


def document_in_scope(session: TaskSession, document: dict) -> bool:
    """A document is fetchable iff the orchestrator allowlisted it — directly,
    or via the root artifact it was decomposed from. Allowlisting an email
    covers its attachments; it never covers unrelated documents."""
    allowed = set(session.document_ids)
    return document["document_id"] in allowed or document["root_document_id"] in allowed


def record_fetch(session: TaskSession, envelope_id: str) -> None:
    """The manifest entry. Committed before the caller sees a byte of text."""
    session.store.record_fetch(session.task_id, envelope_id)


def manifest_for(session: TaskSession) -> ContextManifest:
    """Every envelope the agent has fetched in this task. The agent does not
    write this and cannot omit entries."""
    return ContextManifest(
        run_id=session.task_id,
        envelope_ids=session.store.task_manifest_envelope_ids(session.task_id),
        captured_at=datetime.now(timezone.utc),
    )


def context_envelopes(session: TaskSession) -> dict[str, SourceEnvelope]:
    """Manifest envelopes plus their parent chains, loaded from the store.

    Parents are supplied so lineage can be walked even when the agent fetched
    only a child. Envelopes the agent never fetched and that are not ancestors
    of one it fetched are NOT supplied: citing an envelope outside this dict
    fails verification, which is the correct fate of a citation to content
    that never entered context through us.
    """
    envelopes: dict[str, SourceEnvelope] = {}
    stack = session.store.task_manifest_envelope_ids(session.task_id)
    while stack:
        envelope_id = stack.pop()
        if envelope_id in envelopes:
            continue
        envelope = session.store.get_envelope(envelope_id)
        if envelope is None:
            continue  # left absent; evaluation fails closed on the gap
        envelopes[envelope_id] = envelope
        if envelope.parent_envelope_id is not None:
            stack.append(envelope.parent_envelope_id)
    return envelopes
