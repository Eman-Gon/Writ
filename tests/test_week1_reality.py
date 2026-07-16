from __future__ import annotations

import base64
import asyncio
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from api import create_app
from connectors.gmail import GmailClient, GmailIngestor, MessageState, RawGmailMessage, inspect_transport
from connectors.writ_api import PipelineAuth, PipelineClient
from gateway.broker import Broker
from gateway.store import Store
from harness.foreign_agent.run import (
    MCP_SERVER_NAME,
    SYSTEM_PROMPT,
    USER_PROMPT,
    HarnessConfig,
    _verify_local_manifest,
    analyze_transcript,
    build_agent_environment,
    build_claude_command,
    build_mcp_config,
)
from schemas import AuthenticationAssurance, IngestionChannel
from mcp.server import WritMCPServer
from wire import (
    MCP_TOOLS,
    TOOL_FETCH_DOCUMENT,
    TOOL_LIST_DOCUMENTS,
    TOOL_PROPOSE_MUTATION,
    IngestRequest,
    IngestResponse,
    TaskRequest,
)


class FakeResponse:
    def __init__(self, value: dict[str, Any]) -> None:
        self.body = json.dumps(value).encode()

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


class LocalResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "LocalResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


def test_pipeline_client_matches_fable_http_surface(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "writ.db"))
    app = create_app(store, "pipeline-secret")

    async def dispatch(request: Any) -> bytes:
        path = urllib.parse.urlsplit(request.full_url).path
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://fable.test") as fable:
            response = await fable.request(
                request.method,
                path,
                headers=dict(request.header_items()),
                content=request.data,
            )
            assert response.status_code < 400, response.text
            return response.content

    def open_url(request: Any, _timeout: float) -> LocalResponse:
        return LocalResponse(asyncio.run(dispatch(request)))

    pipeline = PipelineClient(
        "http://fable.test",
        PipelineAuth("pipeline-secret"),
        open_url=open_url,
    )
    meta = IngestRequest(
        declared_type="message/rfc822",
        ingestion_channel=IngestionChannel.INBOUND_EMAIL,
        origin_principal="vendor@example.test",
        authentication_assurance=AuthenticationAssurance.NONE,
        received_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )
    ingested = pipeline.ingest(
        b"From: vendor@example.test\r\nContent-Type: text/plain\r\n\r\nInvoice total: 42.00",
        meta,
        filename="invoice.eml",
        content_type="message/rfc822",
    )
    task = pipeline.create_task(
        TaskRequest(task_ref="invoice-real-1", document_ids=[ingested.document_id])
    )

    assert ingested.envelopes
    assert ingested.root_envelope_id == ingested.envelopes[0].envelope_id
    assert task.task_token.startswith("wt_")

    # Exercise Fable's actual stdio server object through the same JSON-RPC
    # messages a stock MCP client emits.
    server = WritMCPServer(store, Broker({}, store=store), task.task_token)
    listed_tools = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed_tools is not None
    assert {tool["name"] for tool in listed_tools["result"]["tools"]} == set(MCP_TOOLS)

    listed_documents = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": TOOL_LIST_DOCUMENTS, "arguments": {"queue": ""}},
        }
    )
    assert listed_documents is not None
    documents = listed_documents["result"]["structuredContent"]["documents"]
    assert [document["document_id"] for document in documents] == [ingested.document_id]
    assert store.task_manifest_envelope_ids(task.task_id) == []

    fetched = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": TOOL_FETCH_DOCUMENT,
                "arguments": {"document_id": ingested.document_id},
            },
        }
    )
    assert fetched is not None
    fetched_content = fetched["result"]["structuredContent"]
    assert fetched_content["envelope_id"] == ingested.root_envelope_id
    assert store.task_manifest_envelope_ids(task.task_id) == [ingested.root_envelope_id]

    span_start = fetched_content["text"].index("42.00")
    proposed = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": TOOL_PROPOSE_MUTATION,
                "arguments": {
                    "destination_system": "ap_ledger",
                    "field_path": "ap.invoice.amount",
                    "proposed_value": "42.00",
                    "evidence": [
                        {
                            "envelope_id": ingested.root_envelope_id,
                            "span_start": span_start,
                            "span_end": span_start + len("42.00"),
                            "extraction_path": "text_layer",
                        }
                    ],
                },
            },
        }
    )
    assert proposed is not None
    assert proposed["result"]["isError"] is False
    assert proposed["result"]["structuredContent"]["proposal_id"].startswith("prop_")
    assert store.task_manifest_envelope_ids(task.task_id) == [ingested.root_envelope_id]


def test_pipeline_client_sends_untouched_file_and_wire_meta() -> None:
    seen: list[Any] = []

    def open_url(request: Any, timeout: float) -> FakeResponse:
        seen.append((request, timeout))
        return FakeResponse(
            {
                "document_id": "doc_1",
                "envelopes": [{"envelope_id": "env_1", "artifact_type": "email"}],
                "root_envelope_id": "env_1",
                "parse_paths_agree": True,
            }
        )

    client = PipelineClient(
        "https://writ.example/",
        PipelineAuth("pipeline-secret"),
        open_url=open_url,
    )
    raw = b"From: vendor@example.com\r\n\r\nraw-message-body\x00"
    meta = IngestRequest(
        declared_type="message/rfc822",
        ingestion_channel=IngestionChannel.INBOUND_EMAIL,
        origin_principal="vendor@example.com",
        authentication_assurance=AuthenticationAssurance.NONE,
        received_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )
    result = client.ingest(raw, meta, filename="message.eml", content_type="message/rfc822")

    request, timeout = seen[0]
    assert request.full_url == "https://writ.example/v1/ingest"
    assert request.get_header("Authorization") == "Bearer pipeline-secret"
    assert timeout == 30.0
    assert raw in request.data
    assert b'name="file"' in request.data
    assert b'name="meta"' in request.data
    assert b'"declared_type":"message/rfc822"' in request.data
    assert result.document_id == "doc_1"


def test_reality_surface_does_not_reimplement_owned_components() -> None:
    gmail_source = Path("connectors/gmail.py").read_text().lower()
    harness_source = Path("harness/foreign_agent/run.py").read_text().lower()
    assert "connector.ingest" not in gmail_source
    assert "openai" not in gmail_source
    assert "anthropic" not in gmail_source
    assert "agent.ap_agent" not in harness_source


def test_pipeline_client_mints_task_from_wire_contract() -> None:
    seen: list[Any] = []

    def open_url(request: Any, _timeout: float) -> FakeResponse:
        seen.append(request)
        return FakeResponse(
            {
                "task_id": "task_1",
                "task_token": "agent-only-token",
                "expires_at": "2026-07-16T20:00:00Z",
            }
        )

    client = PipelineClient(
        "https://writ.example",
        PipelineAuth("pipeline-secret", header="X-Pipeline-Credential", scheme=""),
        open_url=open_url,
    )
    response = client.create_task(TaskRequest(task_ref="invoice-1", document_ids=["doc_1"]))

    request = seen[0]
    assert request.get_header("X-pipeline-credential") == "pipeline-secret"
    assert json.loads(request.data)["document_ids"] == ["doc_1"]
    assert response.task_token == "agent-only-token"


class FakeGmail:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def list_message_ids(self, *, query: str, max_messages: int) -> list[str]:
        assert query == "is:unread"
        assert max_messages == 10
        return ["gmail_1"]

    def get_raw_message(self, message_id: str) -> RawGmailMessage:
        assert message_id == "gmail_1"
        return RawGmailMessage(
            message_id,
            self.raw,
            datetime(2026, 7, 16, 19, 0, tzinfo=timezone.utc),
        )


class FakeWrit:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def ingest(self, raw: bytes, meta: IngestRequest, **kwargs: Any) -> IngestResponse:
        self.calls.append((raw, meta, kwargs))
        return IngestResponse.model_validate(
            {
                "document_id": "doc_real",
                "envelopes": [
                    {"envelope_id": "env_email", "artifact_type": "email"},
                    {
                        "envelope_id": "env_attachment",
                        "artifact_type": "invoice",
                        "parent_envelope_id": "env_email",
                    },
                ],
                "root_envelope_id": "env_email",
                "parse_paths_agree": True,
            }
        )


def test_gmail_ingestor_forwards_whole_message_without_decomposition(tmp_path: Path) -> None:
    raw = (
        b"From: Vendor Billing <billing@vendor.example>\r\n"
        b"Authentication-Results: mx.google.com; dkim=pass; spf=pass; dmarc=pass\r\n"
        b"Content-Type: multipart/mixed; boundary=x\r\n\r\n"
        b"--x\r\nContent-Type: application/pdf\r\n\r\nPDF-BYTES\r\n--x--\r\n"
    )
    writ = FakeWrit()
    state = MessageState(tmp_path / "gmail-state.json")
    ingestor = GmailIngestor(FakeGmail(raw), writ, state)  # type: ignore[arg-type]

    results = ingestor.poll_once(query="is:unread", max_messages=10)

    assert len(results) == 1
    assert len(writ.calls) == 1
    submitted_raw, meta, kwargs = writ.calls[0]
    assert submitted_raw == raw
    assert meta.origin_principal == "billing@vendor.example"
    assert meta.authentication_assurance == AuthenticationAssurance.PASSWORD
    assert meta.declared_type == "message/rfc822"
    assert kwargs["content_type"] == "message/rfc822"
    assert state.contains("gmail_1")

    # Polling again does not duplicate an accepted message.
    assert ingestor.poll_once(query="is:unread", max_messages=10) == []
    assert len(writ.calls) == 1


def test_untrusted_authentication_results_do_not_inflate_assurance() -> None:
    raw = (
        b"From: attacker@example.test\r\n"
        b"Authentication-Results: attacker.example; dkim=pass; spf=pass; dmarc=pass\r\n\r\n"
    )
    sender, assurance = inspect_transport(raw)
    assert sender == "attacker@example.test"
    assert assurance == AuthenticationAssurance.NONE

    forged_after_real_failure = (
        b"From: attacker@example.test\r\n"
        b"Authentication-Results: mx.google.com; dkim=fail; spf=fail; dmarc=fail\r\n"
        b"Authentication-Results: mx.google.com; dkim=pass; spf=pass; dmarc=pass\r\n\r\n"
    )
    _, assurance = inspect_transport(forged_after_real_failure)
    assert assurance == AuthenticationAssurance.NONE


def test_gmail_raw_payload_decoding_shape() -> None:
    raw = b"From: a@example.test\r\n\r\nhello"
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    seen: list[Any] = []

    def open_url(request: Any, _timeout: float) -> FakeResponse:
        seen.append(request)
        return FakeResponse({"raw": encoded, "internalDate": "1784232000000"})

    gmail = GmailClient("gmail-token", open_url=open_url)
    message = gmail.get_raw_message("message/with spaces")

    assert message.raw_bytes == raw
    assert message.received_at.tzinfo == timezone.utc
    assert "message%2Fwith%20spaces?format=raw" in seen[0].full_url
    assert seen[0].get_header("Authorization") == "Bearer gmail-token"


def test_foreign_agent_has_only_imported_writ_tools(tmp_path: Path) -> None:
    config = build_mcp_config("https://writ.example/mcp")
    assert set(config["mcpServers"]) == {MCP_SERVER_NAME}
    server = config["mcpServers"][MCP_SERVER_NAME]
    assert server["headers"]["Authorization"] == "Bearer ${WRIT_TASK_TOKEN}"

    stdio_config = build_mcp_config(
        None,
        command=("/venv/python", "-m", "mcp.server"),
        server_environment={"STATEGUARD_DB": "/data/writ.db"},
    )
    stdio = stdio_config["mcpServers"][MCP_SERVER_NAME]
    assert stdio == {
        "type": "stdio",
        "command": "/venv/python",
        "args": ["-m", "mcp.server"],
        "env": {
            "STATEGUARD_DB": "/data/writ.db",
            "WRIT_TASK_TOKEN": "${WRIT_TASK_TOKEN}",
        },
    }

    command = build_claude_command(
        "claude",
        tmp_path / "mcp.json",
        model=None,
        max_turns=20,
    )
    tools_index = command.index("--tools")
    assert command[tools_index + 1] == ""
    allowed_index = command.index("--allowedTools")
    assert set(command[allowed_index + 1].split(",")) == {
        f"mcp__{MCP_SERVER_NAME}__{name}" for name in MCP_TOOLS
    }
    assert "--strict-mcp-config" in command
    assert "writ" not in SYSTEM_PROMPT.lower()
    assert "writ" not in USER_PROMPT.lower()
    assert "evidence" not in SYSTEM_PROMPT.lower() + USER_PROMPT.lower()

    environment = build_agent_environment(
        {
            "PATH": "/bin",
            "ANTHROPIC_API_KEY": "agent-runtime-key",
            "GMAIL_ACCESS_TOKEN": "gmail-secret",
            "WRIT_PIPELINE_CREDENTIAL": "pipeline-secret",
        },
        "task-token",
    )
    assert environment["WRIT_TASK_TOKEN"] == "task-token"
    assert environment["ANTHROPIC_API_KEY"] == "agent-runtime-key"
    assert "GMAIL_ACCESS_TOKEN" not in environment
    assert "WRIT_PIPELINE_CREDENTIAL" not in environment

    leaked_surface = analyze_transcript(
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "tools": [
                    *[f"mcp__{MCP_SERVER_NAME}__{name}" for name in MCP_TOOLS],
                    "Read",
                ],
            }
        )
    )
    assert leaked_surface["tool_surface_matches_wire"] is False
    assert leaked_surface["discovered_builtin_tools"] == ["Read"]


def test_local_harness_verifies_the_server_manifest(tmp_path: Path) -> None:
    database = tmp_path / "manifest.db"
    store = Store(str(database))
    store.create_task(
        "task_1",
        "invoice-1",
        "unused-token-hash",
        "2099-01-01T00:00:00+00:00",
        [],
    )
    store.record_fetch("task_1", "env_1")
    config = HarnessConfig(
        api_base_url="http://fable.test",
        pipeline_auth=PipelineAuth("pipeline-secret"),
        task_ref="invoice-1",
        document_ids=("doc_1",),
        mcp_environment=(("STATEGUARD_DB", str(database)),),
    )

    verified, actual, _note = _verify_local_manifest(config, "task_1", ["env_1"])
    assert verified is True
    assert actual == ["env_1"]

    verified, actual, _note = _verify_local_manifest(config, "task_1", ["env_other"])
    assert verified is False
    assert actual == ["env_1"]


def test_transcript_reports_uncoached_fetch_and_valid_evidence() -> None:
    fetch_name = f"mcp__{MCP_SERVER_NAME}__{TOOL_FETCH_DOCUMENT}"
    propose_name = f"mcp__{MCP_SERVER_NAME}__{TOOL_PROPOSE_MUTATION}"
    events = [
        {
            "type": "system",
            "subtype": "init",
            "tools": [f"mcp__{MCP_SERVER_NAME}__{name}" for name in MCP_TOOLS],
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "fetch_1",
                        "name": fetch_name,
                        "input": {"document_id": "doc_1"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "fetch_1",
                        "content": json.dumps(
                            {"envelope_id": "env_1", "artifact_type": "invoice", "text": "Invoice 1"}
                        ),
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "proposal_1",
                        "name": propose_name,
                        "input": {
                            "destination_system": "ap_ledger",
                            "field_path": "ap.invoice.amount",
                            "proposed_value": "4200",
                            "evidence": [
                                {
                                    "envelope_id": "env_1",
                                    "span_start": 8,
                                    "span_end": 9,
                                    "extraction_path": "text_layer",
                                }
                            ],
                        },
                    }
                ]
            },
        },
    ]
    report = analyze_transcript("\n".join(json.dumps(event) for event in events))

    assert report["fetched_envelope_ids"] == ["env_1"]
    assert report["proposed_mutation"] is True
    assert report["evidence_refs_structurally_valid"] is True
    assert report["tool_surface_matches_wire"] is True
    assert report["used_tools_correctly_without_coaching"] is True
    proposal = next(call for call in report["tool_calls"] if call["name"] == propose_name)
    assert proposal["input"]["proposed_value"] == "<omitted from report>"
