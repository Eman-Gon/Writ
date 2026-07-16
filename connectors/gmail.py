"""Poll a real Gmail mailbox and submit raw messages to Writ.

The connector deliberately does not parse attachments or call the local demo
ingestor. It sends the complete RFC 822 artifact to ``POST /v1/ingest`` so the
authorization surface remains the only component that assigns provenance.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Callable, Sequence

from connectors.writ_api import PipelineAuth, PipelineClient
from schemas import AuthenticationAssurance, IngestionChannel
from wire import IngestRequest, IngestResponse


LOGGER = logging.getLogger("writ.connectors.gmail")
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
OpenUrl = Callable[[urllib.request.Request, float], Any]


def _default_open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


class GmailApiError(RuntimeError):
    """Gmail returned an error or a malformed response."""


@dataclass(frozen=True)
class RawGmailMessage:
    message_id: str
    raw_bytes: bytes
    received_at: datetime


@dataclass(frozen=True)
class IngestedGmailMessage:
    gmail_message_id: str
    origin_principal: str
    authentication_assurance: AuthenticationAssurance
    response: IngestResponse


class GmailClient:
    """Minimal Gmail REST client using an externally refreshed OAuth token."""

    def __init__(
        self,
        access_token: str,
        *,
        user_id: str = "me",
        api_base: str = GMAIL_API_BASE,
        timeout: float = 30.0,
        open_url: OpenUrl = _default_open,
    ) -> None:
        self.access_token = access_token
        self.user_id = user_id
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._open_url = open_url

    def list_message_ids(self, *, query: str, max_messages: int) -> list[str]:
        if max_messages < 1:
            return []

        message_ids: list[str] = []
        page_token: str | None = None
        while len(message_ids) < max_messages:
            params = {
                "q": query,
                "maxResults": str(min(500, max_messages - len(message_ids))),
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._get_json(
                f"/users/{urllib.parse.quote(self.user_id, safe='')}/messages?"
                + urllib.parse.urlencode(params)
            )
            for item in payload.get("messages", []):
                message_id = item.get("id")
                if isinstance(message_id, str):
                    message_ids.append(message_id)
            page_token = payload.get("nextPageToken")
            if not isinstance(page_token, str) or not page_token:
                break
        return message_ids[:max_messages]

    def get_raw_message(self, message_id: str) -> RawGmailMessage:
        encoded_id = urllib.parse.quote(message_id, safe="")
        payload = self._get_json(
            f"/users/{urllib.parse.quote(self.user_id, safe='')}/messages/{encoded_id}?format=raw"
        )
        raw = payload.get("raw")
        internal_date = payload.get("internalDate")
        if not isinstance(raw, str) or not isinstance(internal_date, str):
            raise GmailApiError(f"Gmail message {message_id!r} omitted raw or internalDate")
        try:
            padding = "=" * (-len(raw) % 4)
            raw_bytes = base64.urlsafe_b64decode(raw + padding)
            received_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
        except (ValueError, TypeError) as exc:
            raise GmailApiError(f"Gmail message {message_id!r} has invalid raw data") from exc
        return RawGmailMessage(message_id, raw_bytes, received_at)

    def _get_json(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.api_base}{path}",
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
                "User-Agent": "writ-gmail-connector/1.0",
            },
        )
        try:
            with self._open_url(request, self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GmailApiError(f"Gmail returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise GmailApiError(f"Gmail request failed: {exc.reason}") from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise GmailApiError("Gmail returned non-JSON data") from exc
        if not isinstance(payload, dict):
            raise GmailApiError("Gmail returned an unexpected JSON value")
        return payload


class MessageState:
    """Durable local deduplication without modifying the source mailbox."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.processed = self._load()

    def contains(self, message_id: str) -> bool:
        return message_id in self.processed

    def record(self, message_id: str) -> None:
        self.processed.add(message_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(sorted(self.processed), indent=2) + "\n")
        temporary.replace(self.path)

    def _load(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            value = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot read Gmail state file {self.path}: {exc}") from exc
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise RuntimeError(f"Gmail state file {self.path} must contain a JSON string array")
        return set(value)


class GmailIngestor:
    def __init__(self, gmail: GmailClient, writ: PipelineClient, state: MessageState) -> None:
        self.gmail = gmail
        self.writ = writ
        self.state = state

    def poll_once(self, *, query: str, max_messages: int) -> list[IngestedGmailMessage]:
        ingested: list[IngestedGmailMessage] = []
        for message_id in self.gmail.list_message_ids(query=query, max_messages=max_messages):
            if self.state.contains(message_id):
                continue
            message = self.gmail.get_raw_message(message_id)
            sender, assurance = inspect_transport(message.raw_bytes)
            meta = IngestRequest(
                declared_type="message/rfc822",
                ingestion_channel=IngestionChannel.INBOUND_EMAIL,
                origin_principal=sender,
                authentication_assurance=assurance,
                received_at=message.received_at,
            )
            response = self.writ.ingest(
                message.raw_bytes,
                meta,
                filename=f"gmail-{message.message_id}.eml",
                content_type="message/rfc822",
            )
            self.state.record(message.message_id)
            result = IngestedGmailMessage(message.message_id, sender, assurance, response)
            ingested.append(result)
            LOGGER.info(
                "ingested Gmail message %s as document %s (%d envelopes)",
                message.message_id,
                response.document_id,
                len(response.envelopes),
            )
            if not response.parse_paths_agree:
                LOGGER.error(
                    "FINDING: parse_paths_agree=False for Gmail message %s, document %s",
                    message.message_id,
                    response.document_id,
                )
        return ingested


def inspect_transport(raw_message: bytes) -> tuple[str, AuthenticationAssurance]:
    """Return the sender and a deliberately conservative transport assurance.

    Gmail-authenticated DMARC, or both DKIM and SPF, maps only to PASSWORD. The
    schema has no email-transport assurance value, and these checks never prove
    MFA or phishing-resistant authentication by the human sender.
    """

    message = BytesParser(policy=policy.default).parsebytes(raw_message, headersonly=True)
    sender = parseaddr(message.get("From", ""))[1] or "email:unknown"
    results = message.get_all("Authentication-Results", [])
    # Gmail prepends its own result. Do not accept a later, sender-supplied
    # header that merely claims to have been written by mx.google.com.
    trusted = (
        results[:1]
        if results and re.match(r"\s*(?:mx\.)?google\.com\s*;", results[0], re.I)
        else []
    )
    statuses: dict[str, str] = {}
    for value in trusted:
        for mechanism, status in re.findall(
            r"\b(dkim|spf|dmarc)\s*=\s*([a-z_]+)", value, flags=re.IGNORECASE
        ):
            statuses.setdefault(mechanism.lower(), status.lower())

    authenticated = statuses.get("dmarc") == "pass" or (
        statuses.get("dkim") == "pass" and statuses.get("spf") == "pass"
    )
    assurance = AuthenticationAssurance.PASSWORD if authenticated else AuthenticationAssurance.NONE
    return sender, assurance


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Poll Gmail and submit raw messages to Writ")
    parser.add_argument("--once", action="store_true", help="poll once, then exit")
    parser.add_argument("--query", default=os.getenv("GMAIL_QUERY", "is:unread"))
    parser.add_argument("--max-messages", type=int, default=int(os.getenv("GMAIL_MAX_MESSAGES", "25")))
    parser.add_argument("--poll-seconds", type=float, default=float(os.getenv("GMAIL_POLL_SECONDS", "30")))
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(os.getenv("GMAIL_STATE_FILE", ".writ-gmail-state.json")),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    gmail_token = _required_env("GMAIL_ACCESS_TOKEN")
    writ_base = _required_env("WRIT_API_BASE_URL")
    pipeline_credential = _required_env("WRIT_PIPELINE_CREDENTIAL")
    gmail = GmailClient(gmail_token, user_id=os.getenv("GMAIL_USER_ID", "me"))
    writ = PipelineClient(
        writ_base,
        PipelineAuth(
            pipeline_credential,
            header=os.getenv("WRIT_PIPELINE_HEADER", "Authorization"),
            scheme=os.getenv("WRIT_PIPELINE_SCHEME", "Bearer"),
        ),
    )
    ingestor = GmailIngestor(gmail, writ, MessageState(args.state_file))

    while True:
        results = ingestor.poll_once(query=args.query, max_messages=args.max_messages)
        for result in results:
            print(
                json.dumps(
                    {
                        "gmail_message_id": result.gmail_message_id,
                        "origin_principal": result.origin_principal,
                        "authentication_assurance": result.authentication_assurance.value,
                        **result.response.model_dump(mode="json"),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        if args.once:
            return 0
        time.sleep(args.poll_seconds)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
