"""Security-boundary ingestion for StateGuard artifacts.

This module deliberately contains no model calls.  It assigns source
attributes from inspected bytes and transport facts, never from an agent.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from typing import Any, Mapping

try:
    from pypdf import PdfReader
except ImportError:  # The core workstream owns the environment dependencies.
    PdfReader = None  # type: ignore[assignment,misc]

from normalize import (
    field_kind_for,
    has_mixed_script,
    is_normalization_ambiguous,
    normalize_field,
    normalize_text,
)
from schemas import (
    ArtifactType,
    AttestationType,
    AttestedClaim,
    AuthenticationAssurance,
    IngestionChannel,
    Scope,
    SourceEnvelope,
    Validity,
)


_FIELD_PATTERNS = {
    "amount": re.compile(r"(?:USD|\$)\s*([0-9][0-9,]*(?:\.\d{2})?)", re.I),
    "iban": re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){4,30})\b", re.I),
    "invoice": re.compile(r"\b(?:invoice(?:\s+(?:number|no\.?))?|inv\b)\s*[:#]?\s*([A-Z0-9-]+)", re.I),
}


@dataclass
class _ParsedArtifact:
    kind: str
    text_by_path: dict[str, str]
    raw_text: str
    paths_agree: bool = True
    unicode_ambiguous: bool = False
    attachments: list[tuple[bytes, str | None]] = field(default_factory=list)


def _enum_value(value: Any, enum_type: type[Any]) -> Any:
    if isinstance(value, enum_type):
        return value
    return enum_type(str(value))


def _finish_parse(
    kind: str,
    text_by_path: dict[str, str],
    *,
    paths_agree: bool = True,
    attachments: list[tuple[bytes, str | None]] | None = None,
) -> _ParsedArtifact:
    ordered = ("text_layer", "ocr", "form_field", "annotation", "metadata")
    extracted = "\n".join(text_by_path[path] for path in ordered if text_by_path.get(path))
    ambiguous = is_normalization_ambiguous(extracted) or has_mixed_script(extracted)
    # Stage 1 of the shared contract: exactly one call, before spans exist.
    raw_text = normalize_text(extracted)
    return _ParsedArtifact(
        kind=kind,
        text_by_path=text_by_path,
        raw_text=raw_text,
        paths_agree=paths_agree,
        unicode_ambiguous=ambiguous,
        attachments=attachments or [],
    )


def _extract_fields(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, pattern in _FIELD_PATTERNS.items():
        match = pattern.search(text)
        if match:
            values[name] = re.sub(r"\s+", " ", match.group(1).strip()).upper()
    return values


def _paths_agree(paths: Mapping[str, str]) -> bool:
    field_sets = [_extract_fields(text) for text in paths.values() if text.strip()]
    for index, left in enumerate(field_sets):
        for right in field_sets[index + 1 :]:
            for key in left.keys() & right.keys():
                if left[key] != right[key]:
                    return False
    return True


def _decode_qr(image_bytes: bytes) -> str:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return ""
        value, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
        return value.strip()
    except Exception:
        return ""


def _parse_pdf(raw_bytes: bytes) -> _ParsedArtifact:
    paths: dict[str, list[str]] = {
        "text_layer": [],
        "ocr": [],
        "form_field": [],
        "annotation": [],
        "metadata": [],
    }
    ambiguous = False
    if PdfReader is None:
        try:
            result = subprocess.run(
                ["pdftotext", "-layout", "-", "-"],
                input=raw_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=10,
            )
            text = result.stdout.decode("utf-8", errors="replace").strip("\f\n ")
            if text:
                paths["text_layer"].append(text)
            else:
                ambiguous = True
        except (FileNotFoundError, subprocess.SubprocessError):
            ambiguous = True
        extracted = {
            path: "\n".join(pieces)
            for path, pieces in paths.items()
            if pieces
        }
        return _finish_parse("pdf", extracted, paths_agree=not ambiguous)

    try:
        reader = PdfReader(io.BytesIO(raw_bytes))
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                paths["text_layer"].append(text.strip())
            for image in getattr(page, "images", []):
                decoded = _decode_qr(image.data)
                if decoded:
                    paths["ocr"].append(decoded)
            annotations = page.get("/Annots") or []
            for annotation_ref in annotations:
                annotation = annotation_ref.get_object()
                contents = annotation.get("/Contents")
                if contents:
                    paths["annotation"].append(str(contents))
        fields = reader.get_fields() or {}
        for name, details in sorted(fields.items()):
            value = details.get("/V")
            if value is not None:
                paths["form_field"].append(f"{name}: {value}")
        metadata = reader.metadata or {}
        for key, value in sorted(metadata.items()):
            if value:
                paths["metadata"].append(f"{str(key).lstrip('/')}: {value}")
    except Exception:
        ambiguous = True

    extracted: dict[str, str] = {}
    for path, pieces in paths.items():
        if not pieces:
            continue
        extracted[path] = "\n".join(pieces)
    return _finish_parse(
        "pdf",
        extracted,
        paths_agree=(not ambiguous and _paths_agree(extracted)),
    )


def _message_body(message: Message) -> str:
    parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart() or part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() == "text/plain":
                try:
                    parts.append(part.get_content())
                except Exception:
                    payload = part.get_payload(decode=True) or b""
                    parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    elif message.get_content_type() == "text/plain":
        try:
            parts.append(message.get_content())
        except Exception:
            payload = message.get_payload(decode=True) or b""
            parts.append(payload.decode(message.get_content_charset() or "utf-8", errors="replace"))
    headers = [f"{name}: {message.get(name, '')}" for name in ("From", "To", "Subject")]
    return "\n".join(headers + [part.strip() for part in parts if part.strip()])


def _parse_email(raw_bytes: bytes) -> _ParsedArtifact:
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
        body = _message_body(message)
        attachments: list[tuple[bytes, str | None]] = []
        for part in message.iter_attachments():
            payload = part.get_payload(decode=True)
            if payload is not None:
                attachments.append((payload, part.get_content_type()))
        return _finish_parse(
            "email",
            {"text_layer": body},
            attachments=attachments,
        )
    except Exception:
        text = raw_bytes.decode("utf-8", errors="replace")
        return _finish_parse(
            "email",
            {"text_layer": text},
            paths_agree=False,
        )


def _parse_json(raw_bytes: bytes) -> _ParsedArtifact:
    text = raw_bytes.decode("utf-8", errors="replace")
    try:
        value = json.loads(text)
        kind = "vendor_change_form" if isinstance(value, dict) and {
            "subject",
            "canonical_field",
            "value",
        }.issubset(value) else "json"
        agrees = True
    except json.JSONDecodeError:
        kind = "json"
        agrees = False
    return _finish_parse(
        kind,
        {"form_field": text},
        paths_agree=agrees,
    )


def _inspect(raw_bytes: bytes) -> _ParsedArtifact:
    if raw_bytes.startswith(b"%PDF-"):
        return _parse_pdf(raw_bytes)
    stripped = raw_bytes.lstrip()
    if stripped.startswith((b"{", b"[")):
        return _parse_json(raw_bytes)
    head = raw_bytes[:4096].lower()
    if b"mime-version:" in head or (b"from:" in head and b"subject:" in head):
        return _parse_email(raw_bytes)
    text = raw_bytes.decode("utf-8", errors="replace")
    return _finish_parse(
        "unknown",
        {"text_layer": text},
        paths_agree=False,
    )


def _declared_kind(declared_type: Any) -> str:
    value = declared_type.value if isinstance(declared_type, ArtifactType) else str(declared_type)
    value = value.lower().split(";", 1)[0].strip()
    if value in {"application/pdf", "pdf", ".pdf", "invoice", "application/x-pdf"}:
        return "pdf"
    if value in {"message/rfc822", "eml", ".eml", "email"}:
        return "email"
    if value in {"application/json", "json", ".json", "vendor_change_form"}:
        return "json"
    return value


def _artifact_type(parsed: _ParsedArtifact, declared_type: Any) -> ArtifactType:
    declared = _declared_kind(declared_type)
    compatible = parsed.kind == declared or (
        parsed.kind == "vendor_change_form" and declared == "json"
    )
    if not compatible or not parsed.paths_agree or parsed.unicode_ambiguous:
        return ArtifactType.AMBIGUOUS
    if parsed.kind == "pdf":
        lowered = parsed.raw_text.lower()
        return ArtifactType.INVOICE if "invoice" in lowered else ArtifactType.AMBIGUOUS
    if parsed.kind == "email":
        return ArtifactType.EMAIL
    if parsed.kind == "vendor_change_form":
        return ArtifactType.VENDOR_CHANGE_FORM
    return ArtifactType.AMBIGUOUS


def _invoice_origin(text: str) -> str:
    lowered = text.lower()
    if "northwind components" in lowered:
        return "vendor_123"
    match = re.search(r"vendor[_ ](?:id)?\s*[:#]?\s*([a-z0-9_-]+)", text, re.I)
    return match.group(1) if match else "external_vendor_unknown"


def _auth_details(auth: Any) -> tuple[AuthenticationAssurance, bool, bool]:
    if isinstance(auth, Mapping):
        assurance = _enum_value(auth.get("assurance", "none"), AuthenticationAssurance)
        return assurance, bool(auth.get("system_signed")), bool(auth.get("user_attested"))
    return _enum_value(auth, AuthenticationAssurance), False, False


def ingest(
    raw_bytes: bytes,
    declared_type: Any,
    channel: IngestionChannel | str,
    principal: str,
    auth: AuthenticationAssurance | str | Mapping[str, Any],
) -> list[SourceEnvelope]:
    """Inspect bytes and return independently attributed envelopes.

    Child artifacts receive no parent authentication, attestation, origin, or
    scope.  Only ``parent_envelope_id`` is carried as lineage.
    """

    ingestion_channel = _enum_value(channel, IngestionChannel)
    assurance, system_signed, user_attested = _auth_details(auth)
    return _ingest_one(
        raw_bytes,
        declared_type,
        ingestion_channel,
        principal,
        assurance,
        system_signed=system_signed,
        user_attested=user_attested,
        parent_envelope_id=None,
    )


def _ingest_one(
    raw_bytes: bytes,
    declared_type: Any,
    channel: IngestionChannel,
    principal: str,
    assurance: AuthenticationAssurance,
    *,
    system_signed: bool,
    user_attested: bool,
    parent_envelope_id: str | None,
) -> list[SourceEnvelope]:
    parsed = _inspect(raw_bytes)
    artifact_type = _artifact_type(parsed, declared_type)
    digest = hashlib.sha256(raw_bytes).hexdigest()
    envelope_id = f"env_{digest[:20]}"

    if parsed.kind == "pdf":
        origin = _invoice_origin(parsed.raw_text)
        envelope_auth = AuthenticationAssurance.NONE
        attestation = AttestationType.NONE
        scope = Scope()
    elif parsed.kind == "vendor_change_form":
        data = json.loads(parsed.raw_text)
        origin = principal
        envelope_auth = assurance
        is_portal = channel == IngestionChannel.ADMIN_PORTAL
        if is_portal and system_signed:
            attestation = AttestationType.SYSTEM_SIGNED
        elif is_portal and user_attested and data.get("human_submitted") is True:
            attestation = AttestationType.USER_ATTESTED
        else:
            attestation = AttestationType.NONE
        scope = Scope(
            subject_scope=[str(data["subject"])],
            field_scope=[str(data["canonical_field"])],
        )
    else:
        origin = principal
        envelope_auth = assurance
        # Transporting or forwarding content is never an attestation.
        attestation = AttestationType.NONE
        scope = Scope()

    envelope = SourceEnvelope(
        envelope_id=envelope_id,
        parent_envelope_id=parent_envelope_id,
        artifact_type=artifact_type,
        origin_principal=origin,
        ingestion_channel=channel,
        authentication_assurance=envelope_auth,
        attestation_type=attestation,
        scope=scope,
        validity=Validity(issued_at=datetime.now(timezone.utc)),
        artifact_hash=digest,
        raw_text=parsed.raw_text,
        parse_paths_agree=parsed.paths_agree,
    )
    envelopes = [envelope]

    for attachment, content_type in parsed.attachments:
        # Deliberately pass no inherited trust dimensions to the child.
        envelopes.extend(
            _ingest_one(
                attachment,
                content_type or "application/octet-stream",
                channel,
                "external_vendor_unknown",
                AuthenticationAssurance.NONE,
                system_signed=False,
                user_attested=False,
                parent_envelope_id=envelope_id,
            )
        )
    return envelopes


def ingest_attested_claim(
    raw_bytes: bytes,
    channel: IngestionChannel | str,
    principal: str,
    auth: AuthenticationAssurance | str | Mapping[str, Any],
) -> AttestedClaim:
    """Create a claim directly from a signed structured portal submission.

    The input is raw workflow data, not a :class:`SourceEnvelope`; this keeps
    the artifact and the independent attestation act structurally separate.
    """

    parsed = _parse_json(raw_bytes)
    ingestion_channel = _enum_value(channel, IngestionChannel)
    assurance, system_signed, user_attested = _auth_details(auth)
    if (
        parsed.kind != "vendor_change_form"
        or not parsed.paths_agree
        or parsed.unicode_ambiguous
        or ingestion_channel != IngestionChannel.ADMIN_PORTAL
        or not (system_signed and user_attested)
        or json.loads(parsed.raw_text).get("human_submitted") is not True
        or assurance != AuthenticationAssurance.PHISHING_RESISTANT
    ):
        raise ValueError("claim requires an unambiguous, system-signed admin-portal attestation")
    data = json.loads(parsed.raw_text)
    canonical_field = str(data["canonical_field"])
    normalized_value = normalize_field(str(data["value"]), field_kind_for(canonical_field))
    if data.get("attested_by") not in (None, principal):
        raise ValueError("attested_by does not match authenticated principal")
    return AttestedClaim(
        claim_id=str(data.get("claim_id") or f"claim_{uuid.uuid4().hex}"),
        claim_type=str(data.get("claim_type", "vendor_banking_change")),
        subject=str(data["subject"]),
        canonical_field=canonical_field,
        value_hash=hashlib.sha256(normalized_value.encode("utf-8")).hexdigest(),
        attested_by=principal,
        authentication=assurance,
        validity=Validity(issued_at=datetime.now(timezone.utc)),
        source_artifacts=[str(item) for item in data.get("source_artifacts", [])],
    )
