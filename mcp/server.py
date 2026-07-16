"""Writ MCP server — the agent's only surface. Four tools, task-token auth.

JSON-RPC 2.0 over stdio, newline-delimited, implementing the MCP methods a
stock client needs: initialize, tools/list, tools/call, ping. Hand-rolled on
purpose: this package is named `mcp` by the workstream layout, so the MCP SDK
package of the same name cannot be imported from inside the repo anyway, and
the server side of the protocol is small.

What this module may know is deliberately bounded. It imports wire shapes,
sessions, and gateway.propose — nothing else from the decision path. Denials
leave here as "Request denied." and a decision value; every internal signal
stays behind gateway.propose. A source-scan test pins this.

The task token is bound at process start (the orchestrator launches the agent
with it in env), resolved freshly on every call so expiry is enforced per call.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from schemas import Decision, ProposedMutation
from wire import (
    MCP_TOOLS,
    TOOL_CHECK_STATUS,
    TOOL_FETCH_DOCUMENT,
    TOOL_LIST_DOCUMENTS,
    TOOL_PROPOSE_MUTATION,
    WIRE_VERSION,
    CheckStatusParams,
    CheckStatusResult,
    DocumentRef,
    FetchDocumentParams,
    FetchDocumentResult,
    ListDocumentsParams,
    ListDocumentsResult,
    ProposeMutationParams,
    ProposeMutationResult,
)

from gateway.broker import Broker
from gateway.propose import propose_and_commit
from gateway.store import Store
from mcp.session import (
    TaskSession,
    context_envelopes,
    document_in_scope,
    manifest_for,
    record_fetch,
    resolve_session,
)

PROTOCOL_VERSION = "2025-03-26"

# One string for "does not exist", "not allowlisted", and "content missing":
# distinguishing them would tell the agent what exists outside its task.
_DOCUMENT_UNAVAILABLE = "document not available to this task"
_PROPOSAL_UNKNOWN = "unknown proposal_id for this task"
_TOKEN_INVALID = "task token invalid or expired"


class ToolRefused(Exception):
    """Refusal with an agent-safe message. Nothing internal rides on it."""


_PARAM_MODELS: dict[str, type[BaseModel]] = {
    TOOL_LIST_DOCUMENTS: ListDocumentsParams,
    TOOL_FETCH_DOCUMENT: FetchDocumentParams,
    TOOL_PROPOSE_MUTATION: ProposeMutationParams,
    TOOL_CHECK_STATUS: CheckStatusParams,
}

_TOOL_DESCRIPTIONS = {
    TOOL_LIST_DOCUMENTS: "List documents available to this task. Returns references only, no content.",
    TOOL_FETCH_DOCUMENT: "Fetch a document's text by document_id. Only documents in this task's scope.",
    TOOL_PROPOSE_MUTATION: (
        "Propose a change to a destination field, citing evidence spans "
        "(character offsets into fetched document text). Writ decides and, if "
        "allowed, executes."
    ),
    TOOL_CHECK_STATUS: "Check the status of a previously submitted proposal.",
}


def _proposal_id(session: TaskSession, params: ProposeMutationParams) -> str:
    """Deterministic per (task, mutation): an agent retrying the same proposal
    lands on the same id, so the broker's idempotency ledger absorbs the retry
    instead of executing twice."""
    payload = json.dumps(
        {
            "task": session.task_id,
            "destination": params.destination_system,
            "field": params.field_path,
            "value": params.proposed_value,
            "evidence": [ref.model_dump() for ref in params.evidence],
        },
        sort_keys=True,
    )
    return "prop_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


class WritMCPServer:
    def __init__(self, store: Store, broker: Broker, task_token: str):
        self._store = store
        self._broker = broker
        self._task_token = task_token
        self._tools: dict[str, Callable[[TaskSession, BaseModel], BaseModel]] = {
            TOOL_LIST_DOCUMENTS: self._list_documents,
            TOOL_FETCH_DOCUMENT: self._fetch_document,
            TOOL_PROPOSE_MUTATION: self._propose_mutation,
            TOOL_CHECK_STATUS: self._check_status,
        }
        assert set(self._tools) == set(MCP_TOOLS)

    # -- the four tools ------------------------------------------------------

    def _list_documents(self, session: TaskSession, params: ListDocumentsParams) -> ListDocumentsResult:
        # Listing is not fetching: nothing here touches the manifest.
        rows = self._store.list_documents(list(session.document_ids), params.queue or None)
        return ListDocumentsResult(
            documents=[
                DocumentRef(
                    document_id=row["document_id"],
                    artifact_type=row["artifact_type"],
                    received_at=row["received_at"],
                    sender_display=row["sender_display"],
                )
                for row in rows
                if document_in_scope(session, row)
            ]
        )

    def _fetch_document(self, session: TaskSession, params: FetchDocumentParams) -> FetchDocumentResult:
        document = self._store.get_document(params.document_id)
        if document is None or not document_in_scope(session, document):
            raise ToolRefused(_DOCUMENT_UNAVAILABLE)
        envelope = self._store.get_envelope(document["envelope_id"])
        if envelope is None:
            raise ToolRefused(_DOCUMENT_UNAVAILABLE)

        # THE MANIFEST ENTRY. Durable before the agent sees a byte; if the
        # return path fails from here on, the record must already exist.
        record_fetch(session, envelope.envelope_id)

        # raw_text verbatim: the offsets the agent computes are the offsets
        # spans are verified against. artifact_type only — no trust fields.
        return FetchDocumentResult(
            envelope_id=envelope.envelope_id,
            artifact_type=envelope.artifact_type,
            text=envelope.raw_text,
        )

    def _propose_mutation(self, session: TaskSession, params: ProposeMutationParams) -> ProposeMutationResult:
        proposal = ProposedMutation(
            proposal_id=_proposal_id(session, params),
            run_id=session.task_id,
            destination_system=params.destination_system,
            field_path=params.field_path,
            proposed_value=params.proposed_value,
            evidence=params.evidence,
        )
        self._store.record_proposal(proposal.proposal_id, session.task_id)
        response, effect = propose_and_commit(
            proposal,
            manifest_for(session),
            context_envelopes(session),
            broker=self._broker,
            store=self._store,
        )
        return ProposeMutationResult(
            proposal_id=response.proposal_id,
            decision=response.decision,
            message=response.message,
            effect=effect,
        )

    def _check_status(self, session: TaskSession, params: CheckStatusParams) -> CheckStatusResult:
        if self._store.get_proposal_task(params.proposal_id) != session.task_id:
            raise ToolRefused(_PROPOSAL_UNKNOWN)
        status = self._store.proposal_status(params.proposal_id)
        if status is None:
            raise ToolRefused(_PROPOSAL_UNKNOWN)
        decision, effect = status
        return CheckStatusResult(
            proposal_id=params.proposal_id,
            decision=Decision(decision),
            effect=effect,
        )

    # -- JSON-RPC / MCP plumbing ----------------------------------------------

    def handle(self, message: dict) -> dict | None:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}

        if method is not None and method.startswith("notifications/"):
            return None
        if method == "initialize":
            return self._result(
                msg_id,
                {
                    "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "writ", "version": WIRE_VERSION},
                },
            )
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": self._tool_specs()})
        if method == "tools/call":
            return self._result(msg_id, self._call_tool(params))
        return self._error(msg_id, -32601, f"method not found: {method}")

    def _tool_specs(self) -> list[dict]:
        return [
            {
                "name": name,
                "description": _TOOL_DESCRIPTIONS[name],
                "inputSchema": _PARAM_MODELS[name].model_json_schema(),
            }
            for name in MCP_TOOLS
        ]

    def _call_tool(self, params: dict) -> dict:
        name = params.get("name")
        tool = self._tools.get(name or "")
        if tool is None:
            return _tool_error(f"unknown tool: {name}")

        session = resolve_session(self._store, self._task_token)
        if session is None:
            return _tool_error(_TOKEN_INVALID)

        try:
            arguments = _PARAM_MODELS[name].model_validate(params.get("arguments") or {})
        except ValidationError:
            return _tool_error("invalid parameters")

        try:
            result = tool(session, arguments)
        except ToolRefused as refusal:
            return _tool_error(str(refusal))

        serialized = result.model_dump_json()
        return {
            "content": [{"type": "text", "text": serialized}],
            "structuredContent": json.loads(serialized),
            "isError": False,
        }

    @staticmethod
    def _result(msg_id: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    def serve_stdio(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                print(json.dumps(self._error(None, -32700, "parse error")), flush=True)
                continue
            response = self.handle(message)
            if response is not None:
                print(json.dumps(response), flush=True)


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def main() -> None:
    """Dev composition root: the ONLY place destination executors meet the
    broker in this process. Sol's real adapter replaces the mock in week 2."""
    from mock.erp import MockERP

    erp = MockERP()

    def _erp_executor(mutation, record):
        return erp.apply(mutation.destination_system, record.canonical_field, mutation.proposed_value)

    store = Store()
    broker = Broker({"vendor_master": _erp_executor, "ap_ledger": _erp_executor}, store=store)
    task_token = os.environ.get("WRIT_TASK_TOKEN", "")
    WritMCPServer(store, broker, task_token).serve_stdio()


if __name__ == "__main__":
    main()
