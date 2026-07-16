"""Small HTTP client for Writ's pipeline-authenticated API surface.

This module only serializes the models frozen in ``wire.py``. It does not
perform ingestion, provenance assignment, task scoping, or evaluation locally.
"""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from wire import IngestRequest, IngestResponse, TaskRequest, TaskResponse


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
OpenUrl = Callable[[urllib.request.Request, float], Any]


class WritApiError(RuntimeError):
    """A Writ API request failed or returned a contract-invalid response."""


def _default_open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


@dataclass(frozen=True)
class PipelineAuth:
    credential: str
    header: str = "Authorization"
    scheme: str = "Bearer"

    def as_header(self) -> tuple[str, str]:
        value = f"{self.scheme} {self.credential}" if self.scheme else self.credential
        return self.header, value


class PipelineClient:
    """Call only the orchestrator-facing ``/v1/*`` endpoints."""

    def __init__(
        self,
        base_url: str,
        auth: PipelineAuth,
        *,
        timeout: float = 30.0,
        open_url: OpenUrl = _default_open,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = timeout
        self._open_url = open_url

    def ingest(
        self,
        raw_bytes: bytes,
        meta: IngestRequest,
        *,
        filename: str,
        content_type: str,
    ) -> IngestResponse:
        boundary = f"writ-{secrets.token_hex(16)}"
        body = _multipart_body(
            boundary,
            raw_bytes=raw_bytes,
            filename=filename,
            content_type=content_type,
            meta_json=meta.model_dump_json(),
        )
        request = self._request(
            "/v1/ingest",
            data=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return self._send(request, IngestResponse)

    def create_task(self, task: TaskRequest) -> TaskResponse:
        request = self._request(
            "/v1/tasks",
            data=task.model_dump_json().encode("utf-8"),
            content_type="application/json",
        )
        return self._send(request, TaskResponse)

    def _request(self, path: str, *, data: bytes, content_type: str) -> urllib.request.Request:
        header_name, header_value = self.auth.as_header()
        return urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method="POST",
            headers={
                header_name: header_value,
                "Accept": "application/json",
                "Content-Type": content_type,
                "User-Agent": "writ-reality-surface/1.0",
            },
        )

    def _send(self, request: urllib.request.Request, response_type: type[ResponseModel]) -> ResponseModel:
        try:
            with self._open_url(request, self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise WritApiError(f"Writ API returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise WritApiError(f"Writ API request failed: {exc.reason}") from exc

        try:
            decoded = json.loads(payload)
            return response_type.model_validate(decoded)
        except (json.JSONDecodeError, ValueError) as exc:
            raise WritApiError(
                f"Writ API response did not match {response_type.__name__}: "
                f"{payload.decode('utf-8', errors='replace')}"
            ) from exc


def _multipart_body(
    boundary: str,
    *,
    raw_bytes: bytes,
    filename: str,
    content_type: str,
    meta_json: str,
) -> bytes:
    """Build exactly two multipart fields: the untouched file and wire meta."""

    safe_filename = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
    marker = boundary.encode("ascii")
    chunks = [
        b"--" + marker + b"\r\n",
        f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        raw_bytes,
        b"\r\n--" + marker + b"\r\n",
        b'Content-Disposition: form-data; name="meta"\r\n',
        b"Content-Type: application/json\r\n\r\n",
        meta_json.encode("utf-8"),
        b"\r\n--" + marker + b"--\r\n",
    ]
    return b"".join(chunks)
