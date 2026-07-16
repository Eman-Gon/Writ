"""Week-1 done-when for the Writ authorization surface.

Identity separation is tested FIRST: if a task_token can reach /v1/ingest the
product is void, so that property leads the file. Then the foreign-agent path,
the manifest-is-the-product reproduction of Act 3 over MCP, opacity, and
manifest durability.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import mcp.server as mcp_server
from api import create_app
from gateway.broker import Broker
from gateway.store import Store
from mcp.server import WritMCPServer
from mock.erp import MockERP
from schemas import (
    ArtifactType,
    AttestationType,
    AuthenticationAssurance,
    Decision,
    SupportQuality,
)
from wire import (
    MCP_TOOLS,
    TOOL_CHECK_STATUS,
    TOOL_FETCH_DOCUMENT,
    TOOL_LIST_DOCUMENTS,
    TOOL_PROPOSE_MUTATION,
    IngestRequest,
    IngestResponse,
    TaskResponse,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
PIPELINE_CREDENTIAL = "pc_test_only_pipeline_credential"

IBAN_PATTERN = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){4,30})\b")

# Internal decision vocabulary that must never appear in anything the agent
# receives, and never in the source of the MCP layer itself.
FORBIDDEN_TOKENS = (
    "DiagnosticResponse",
    "diagnostic",
    "support_quality",
    "catch_type",
    "controls_evaluated",
    "least_trusted",
    "enforcing_control",
    "DecisionRecord",
    "mode=",
)


# -- plumbing ----------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path / "writ.db"))


@pytest.fixture()
def client(store):
    return TestClient(create_app(store, PIPELINE_CREDENTIAL))


@pytest.fixture()
def erp():
    return MockERP()


@pytest.fixture()
def executed(erp):
    calls: list[tuple[str, str]] = []
    return calls


@pytest.fixture()
def broker(store, erp, executed):
    def executor(mutation, record):
        executed.append((record.canonical_field, mutation.proposed_value))
        return erp.apply(mutation.destination_system, record.canonical_field, mutation.proposed_value)

    return Broker({"vendor_master": executor, "ap_ledger": executor}, store=store)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _meta(channel: str, origin: str, auth: str, declared: str) -> str:
    return IngestRequest(
        declared_type=declared,
        ingestion_channel=channel,
        origin_principal=origin,
        authentication_assurance=auth,
        received_at=datetime.now(timezone.utc),
    ).model_dump_json()


def _ingest(client, fixture: str, *, declared: str, channel: str, origin: str, auth: str) -> IngestResponse:
    response = client.post(
        "/v1/ingest",
        files={"file": (fixture, (FIXTURES / fixture).read_bytes())},
        data={"meta": _meta(channel, origin, auth, declared)},
        headers=_bearer(PIPELINE_CREDENTIAL),
    )
    assert response.status_code == 200, response.text
    return IngestResponse.model_validate(response.json())


def _mint_task(client, document_ids: list[str], ttl: int = 3600) -> TaskResponse:
    response = client.post(
        "/v1/tasks",
        json={"task_ref": "test-task", "document_ids": document_ids, "ttl_seconds": ttl},
        headers=_bearer(PIPELINE_CREDENTIAL),
    )
    assert response.status_code == 200, response.text
    return TaskResponse.model_validate(response.json())


def _call(server: WritMCPServer, tool: str, arguments: dict) -> dict:
    """Full JSON-RPC round trip, so opacity tests cover the entire wire shape."""
    response = server.handle(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    )
    return response["result"]


def _payload(result: dict) -> dict:
    assert result["isError"] is False, result["content"]
    return result["structuredContent"]


def _clean_invoice_setup(client, store, broker):
    ingest = _ingest(
        client, "invoice_clean.pdf",
        declared="application/pdf", channel="inbound_email", origin="vendor_123", auth="none",
    )
    task = _mint_task(client, [ingest.document_id])
    return ingest, task, WritMCPServer(store, broker, task.task_token)


def _forwarded_email_setup(client, store, broker):
    ingest = _ingest(
        client, "email_forwarded.eml",
        declared="message/rfc822", channel="inbound_email", origin="finance_admin_456", auth="mfa",
    )
    assert len(ingest.envelopes) == 2  # email + attached invoice, decomposed
    task = _mint_task(client, [ingest.document_id])
    return ingest, task, WritMCPServer(store, broker, task.task_token)


# -- 3. identity separation. Written first; if this fails nothing else matters.


def test_task_token_cannot_ingest(client, store, broker):
    ingest = _ingest(
        client, "invoice_clean.pdf",
        declared="application/pdf", channel="inbound_email", origin="vendor_123", auth="none",
    )
    task = _mint_task(client, [ingest.document_id])

    response = client.post(
        "/v1/ingest",
        files={"file": ("x.pdf", (FIXTURES / "invoice_clean.pdf").read_bytes())},
        data={"meta": _meta("inbound_email", "attacker", "phishing_resistant", "application/pdf")},
        headers=_bearer(task.task_token),
    )
    assert response.status_code in (401, 403)


def test_task_token_cannot_mint_tasks(client):
    seed = _ingest(
        client, "invoice_clean.pdf",
        declared="application/pdf", channel="inbound_email", origin="vendor_123", auth="none",
    )
    task = _mint_task(client, [seed.document_id])

    response = client.post(
        "/v1/tasks",
        json={"task_ref": "self-served", "document_ids": [seed.document_id]},
        headers=_bearer(task.task_token),
    )
    assert response.status_code in (401, 403)


def test_missing_or_wrong_credential_rejected(client):
    for headers in ({}, _bearer("wrong"), {"Authorization": "Basic abc"}):
        assert client.post("/v1/tasks", json={"task_ref": "x", "document_ids": []}, headers=headers).status_code in (401, 403)
        assert (
            client.post(
                "/v1/ingest",
                files={"file": ("x.pdf", b"%PDF-fake")},
                data={"meta": _meta("inbound_email", "x", "none", "application/pdf")},
                headers=headers,
            ).status_code
            in (401, 403)
        )


def test_fetch_outside_allowlist_refused(client, store, broker):
    allowed = _ingest(
        client, "invoice_clean.pdf",
        declared="application/pdf", channel="inbound_email", origin="vendor_123", auth="none",
    )
    other = _ingest(
        client, "vendor_change_form.json",
        declared="application/json", channel="admin_portal", origin="finance_admin_456", auth="phishing_resistant",
    )
    task = _mint_task(client, [allowed.document_id])
    server = WritMCPServer(store, broker, task.task_token)

    refused = _call(server, TOOL_FETCH_DOCUMENT, {"document_id": other.document_id})
    assert refused["isError"] is True
    ghost = _call(server, TOOL_FETCH_DOCUMENT, {"document_id": "doc_does_not_exist"})
    assert ghost["isError"] is True
    # Out-of-scope and nonexistent are indistinguishable: no existence oracle.
    assert refused["content"] == ghost["content"]

    # The refused fetches left no manifest entries.
    assert store.task_manifest_envelope_ids(task.task_id) == []

    # Out-of-scope documents are not listed either.
    listing = _payload(_call(server, TOOL_LIST_DOCUMENTS, {}))
    listed = {doc["document_id"] for doc in listing["documents"]}
    assert other.document_id not in listed


def test_expired_task_token_refused(client, store, broker):
    ingest = _ingest(
        client, "invoice_clean.pdf",
        declared="application/pdf", channel="inbound_email", origin="vendor_123", auth="none",
    )
    task = _mint_task(client, [ingest.document_id], ttl=0)
    server = WritMCPServer(store, broker, task.task_token)
    result = _call(server, TOOL_FETCH_DOCUMENT, {"document_id": ingest.document_id})
    assert result["isError"] is True

    garbage = WritMCPServer(store, broker, "wt_not_a_real_token")
    assert _call(garbage, TOOL_LIST_DOCUMENTS, {})["isError"] is True


# -- 1. the foreign-agent path, end to end -------------------------------------


def test_foreign_agent_path_end_to_end(client, store, broker, erp, executed):
    ingest, task, server = _clean_invoice_setup(client, store, broker)

    listing = _payload(_call(server, TOOL_LIST_DOCUMENTS, {}))
    assert [doc["document_id"] for doc in listing["documents"]] == [ingest.document_id]
    assert "text" not in json.dumps(listing)  # listing returns no content

    fetched = _payload(_call(server, TOOL_FETCH_DOCUMENT, {"document_id": ingest.document_id}))
    text = fetched["text"]
    span = text.index("4200.00")

    proposed = _payload(
        _call(
            server,
            TOOL_PROPOSE_MUTATION,
            {
                "destination_system": "ap_ledger",
                "field_path": "invoice.amount",
                "proposed_value": "4200.00",
                "evidence": [
                    {
                        "envelope_id": fetched["envelope_id"],
                        "span_start": span,
                        "span_end": span + len("4200.00"),
                        "extraction_path": "text_layer",
                    }
                ],
            },
        )
    )
    assert proposed["decision"] in (d.value for d in Decision)
    assert proposed["decision"] == Decision.ALLOW.value
    assert "4,200" in proposed["effect"]
    assert erp.ap_ledger and erp.ap_ledger[0]["amount"] == "4200.00"

    status = _payload(_call(server, TOOL_CHECK_STATUS, {"proposal_id": proposed["proposal_id"]}))
    assert status["decision"] == Decision.ALLOW.value
    assert status["effect"] == proposed["effect"]

    # Idempotent retry: same mutation, same task -> one business effect.
    again = _payload(
        _call(
            server,
            TOOL_PROPOSE_MUTATION,
            {
                "destination_system": "ap_ledger",
                "field_path": "invoice.amount",
                "proposed_value": "4200.00",
                "evidence": [
                    {
                        "envelope_id": fetched["envelope_id"],
                        "span_start": span,
                        "span_end": span + len("4200.00"),
                        "extraction_path": "text_layer",
                    }
                ],
            },
        )
    )
    assert again["proposal_id"] == proposed["proposal_id"]
    assert again["effect"] == proposed["effect"]
    assert len(executed) == 1


# -- 2. THE MANIFEST IS THE PRODUCT ---------------------------------------------


def test_manifest_catches_what_the_citation_omits(client, store, broker, erp):
    """Act 3 over MCP with zero framework hooks. The agent fetches the poisoned
    invoice and the admin's email, cites ONLY the email, and the span passes.
    It must deny because the manifest caught the invoice ancestor — and the
    record must say DERIVED, or it denied for the wrong reason."""
    ingest, task, server = _forwarded_email_setup(client, store, broker)
    original_account = erp.vendors["vendor_123"].bank_account

    listing = _payload(_call(server, TOOL_LIST_DOCUMENTS, {}))
    by_type = {doc["artifact_type"]: doc["document_id"] for doc in listing["documents"]}
    assert ArtifactType.EMAIL.value in by_type and ArtifactType.INVOICE.value in by_type

    invoice = _payload(_call(server, TOOL_FETCH_DOCUMENT, {"document_id": by_type["invoice"]}))
    email = _payload(_call(server, TOOL_FETCH_DOCUMENT, {"document_id": by_type["email"]}))

    match = IBAN_PATTERN.search(email["text"])
    assert match is not None
    span_start, span_end = match.span(1)

    result = _call(
        server,
        TOOL_PROPOSE_MUTATION,
        {
            "destination_system": "vendor_master",
            "field_path": "vendor.remittance.bank_account",
            "proposed_value": match.group(1),
            "evidence": [
                {
                    "envelope_id": email["envelope_id"],
                    "span_start": span_start,
                    "span_end": span_end,
                    "extraction_path": "text_layer",
                }
            ],
        },
    )
    proposed = _payload(result)
    assert proposed["decision"] == Decision.DENY.value
    assert proposed["effect"] is None
    assert erp.vendors["vendor_123"].bank_account == original_account

    # The reason must be lineage, not a failed span. UNVERIFIED here means the
    # architecture is broken and the test would be green for the wrong reason.
    record = store.latest_decision_for_proposal(proposed["proposal_id"])
    assert record.support_quality == SupportQuality.DERIVED
    assert record.least_trusted_ancestor == invoice["envelope_id"]
    assert set(record.context_envelope_ids) == {invoice["envelope_id"], email["envelope_id"]}


def test_citation_without_fetch_gets_nothing(client, store, broker):
    """Out-of-band reading, simulated: the agent knows an envelope_id but never
    fetched it through Writ. The citation fails and nothing is allowed."""
    ingest, task, server = _clean_invoice_setup(client, store, broker)

    proposed = _payload(
        _call(
            server,
            TOOL_PROPOSE_MUTATION,
            {
                "destination_system": "ap_ledger",
                "field_path": "invoice.amount",
                "proposed_value": "4200.00",
                "evidence": [
                    {
                        "envelope_id": ingest.root_envelope_id,
                        "span_start": 0,
                        "span_end": 9,
                        "extraction_path": "text_layer",
                    }
                ],
            },
        )
    )
    assert proposed["decision"] != Decision.ALLOW.value
    record = store.latest_decision_for_proposal(proposed["proposal_id"])
    assert record.support_quality == SupportQuality.UNVERIFIED


def test_listing_is_not_fetching(client, store, broker):
    ingest, task, server = _clean_invoice_setup(client, store, broker)
    _payload(_call(server, TOOL_LIST_DOCUMENTS, {}))
    _payload(_call(server, TOOL_LIST_DOCUMENTS, {"queue": "inbound_email"}))
    assert store.task_manifest_envelope_ids(task.task_id) == []


# -- 4. no diagnostic leakage ---------------------------------------------------


def test_denial_is_opaque_on_the_wire(client, store, broker):
    ingest, task, server = _forwarded_email_setup(client, store, broker)
    listing = _payload(_call(server, TOOL_LIST_DOCUMENTS, {}))
    by_type = {doc["artifact_type"]: doc["document_id"] for doc in listing["documents"]}
    _payload(_call(server, TOOL_FETCH_DOCUMENT, {"document_id": by_type["invoice"]}))
    email = _payload(_call(server, TOOL_FETCH_DOCUMENT, {"document_id": by_type["email"]}))
    match = IBAN_PATTERN.search(email["text"])

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": TOOL_PROPOSE_MUTATION,
                "arguments": {
                    "destination_system": "vendor_master",
                    "field_path": "vendor.remittance.bank_account",
                    "proposed_value": match.group(1),
                    "evidence": [
                        {
                            "envelope_id": email["envelope_id"],
                            "span_start": match.span(1)[0],
                            "span_end": match.span(1)[1],
                            "extraction_path": "text_layer",
                        }
                    ],
                },
            },
        }
    )
    serialized = json.dumps(response)
    payload = response["result"]["structuredContent"]
    assert payload["decision"] == Decision.DENY.value
    assert payload["message"] == "Request denied."
    for token in FORBIDDEN_TOKENS:
        assert token not in serialized, f"denial leaked {token!r}"

    # The status poll is equally opaque.
    status_response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": TOOL_CHECK_STATUS, "arguments": {"proposal_id": payload["proposal_id"]}},
        }
    )
    for token in FORBIDDEN_TOKENS:
        assert token not in json.dumps(status_response)


def test_diagnostics_structurally_unreachable_from_mcp():
    """Not a flag that defaults off: the MCP layer's source must not even name
    the internal decision vocabulary, import the evaluator, or hold a record."""
    mcp_dir = Path(mcp_server.__file__).resolve().parent
    sources = sorted(mcp_dir.glob("*.py"))
    assert sources, "mcp package has no sources?"
    for source in sources:
        text = source.read_text()
        for token in FORBIDDEN_TOKENS + ("gateway.evaluate", "gateway import evaluate"):
            assert token not in text, f"{source.name} contains {token!r}"


def test_tool_surface_is_exactly_four(store, broker):
    server = WritMCPServer(store, broker, "wt_irrelevant")
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert names == list(MCP_TOOLS)
    for token in FORBIDDEN_TOKENS:
        assert token not in json.dumps(listed)


def test_check_status_is_task_scoped(client, store, broker):
    ingest, task, server = _clean_invoice_setup(client, store, broker)
    fetched = _payload(_call(server, TOOL_FETCH_DOCUMENT, {"document_id": ingest.document_id}))
    span = fetched["text"].index("4200.00")
    proposed = _payload(
        _call(
            server,
            TOOL_PROPOSE_MUTATION,
            {
                "destination_system": "ap_ledger",
                "field_path": "invoice.amount",
                "proposed_value": "4200.00",
                "evidence": [
                    {
                        "envelope_id": fetched["envelope_id"],
                        "span_start": span,
                        "span_end": span + len("4200.00"),
                        "extraction_path": "text_layer",
                    }
                ],
            },
        )
    )

    stranger_task = _mint_task(client, [ingest.document_id])
    stranger = WritMCPServer(store, broker, stranger_task.task_token)
    result = _call(stranger, TOOL_CHECK_STATUS, {"proposal_id": proposed["proposal_id"]})
    assert result["isError"] is True


# -- 5. manifest durability ------------------------------------------------------


def test_manifest_entry_survives_fetch_failure(client, store, broker, monkeypatch):
    """If the return path throws after the record, the manifest entry must
    already be durable. A fetch that is not recorded is a laundering hole."""
    ingest, task, server = _clean_invoice_setup(client, store, broker)

    class ExplodesOnBuild:
        def __init__(self, **kwargs):
            raise RuntimeError("return path failed after recording")

    monkeypatch.setattr(mcp_server, "FetchDocumentResult", ExplodesOnBuild)
    with pytest.raises(RuntimeError):
        _call(server, TOOL_FETCH_DOCUMENT, {"document_id": ingest.document_id})

    assert store.task_manifest_envelope_ids(task.task_id) == [ingest.root_envelope_id]

    # And the durable record is what evaluation reads: a proposal in this task
    # now carries that envelope in context even though the agent never got text.
    monkeypatch.undo()
    refetched = _payload(_call(server, TOOL_FETCH_DOCUMENT, {"document_id": ingest.document_id}))
    assert refetched["envelope_id"] == ingest.root_envelope_id


# -- nested decomposition: children inherit nothing -------------------------------


def test_nested_decomposition_children_inherit_nothing(client, store):
    ingest = _ingest(
        client, "email_forwarded.eml",
        declared="message/rfc822", channel="inbound_email", origin="finance_admin_456", auth="mfa",
    )
    assert ingest.parse_paths_agree is True
    by_id = {env.envelope_id: env for env in ingest.envelopes}
    root = by_id.pop(ingest.root_envelope_id)
    (child_ref,) = by_id.values()
    assert child_ref.parent_envelope_id == ingest.root_envelope_id

    email = store.get_envelope(ingest.root_envelope_id)
    child = store.get_envelope(child_ref.envelope_id)
    assert email.authentication_assurance == AuthenticationAssurance.MFA
    # The attachment got NOTHING from its authenticated parent.
    assert child.authentication_assurance == AuthenticationAssurance.NONE
    assert child.attestation_type == AttestationType.NONE
    assert child.origin_principal != email.origin_principal


def test_stdio_protocol_smoke(store, broker):
    server = WritMCPServer(store, broker, "wt_irrelevant")
    init = server.handle(
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    )
    assert init["result"]["serverInfo"]["name"] == "writ"
    assert init["result"]["capabilities"] == {"tools": {}}
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "id": 1, "method": "ping"})["result"] == {}
    unknown = server.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/list"})
    assert unknown["error"]["code"] == -32601
