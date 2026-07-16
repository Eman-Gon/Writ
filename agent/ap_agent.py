"""A deliberately naïve accounts-payable agent.

The adapter, not the model, captures the context manifest.  Extraction is
intentionally credulous: hidden parser-visible instructions are treated as
ordinary invoice content so the gateway has something real to defend against.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Sequence

from schemas import AttestedClaim, ContextManifest, EvidenceRef, ProposedMutation, SourceEnvelope


_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){4,30}\b", re.I)
_AMOUNT = re.compile(r"(?<![A-Z0-9])([0-9][0-9,]*\.\d{2})(?![A-Z0-9])")
_ROUTING_NOTE = re.compile(
    r"(?:note:\s*)?(route payments to IBAN\s+[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){4,30})",
    re.I,
)


@dataclass(frozen=True)
class AgentRun:
    manifest: ContextManifest
    proposals: list[ProposedMutation]


class APAgent:
    """Extract AP changes while knowing nothing about destination credentials."""

    def __init__(self, *, use_dashscope: bool = False, model: str = "qwen-plus") -> None:
        self.use_dashscope = use_dashscope
        self.model = model

    def run(
        self,
        envelopes: Sequence[SourceEnvelope],
        *,
        claim: AttestedClaim | None = None,
        intent: Literal["invoice", "note_alias"] = "invoice",
    ) -> AgentRun:
        run_id = f"run_{uuid.uuid4().hex}"
        # This is adapter-owned and cannot be edited or filtered by the model.
        manifest = ContextManifest(
            run_id=run_id,
            envelope_ids=[envelope.envelope_id for envelope in envelopes],
            captured_at=datetime.now(timezone.utc),
        )

        extracted = self._dashscope_extract(envelopes) if self.use_dashscope else None
        proposal = self._proposal_from_model(run_id, envelopes, extracted, claim, intent)
        if proposal is None:
            proposal = self._naive_proposal(run_id, envelopes, claim, intent)
        return AgentRun(manifest=manifest, proposals=[proposal] if proposal else [])

    def _naive_proposal(
        self,
        run_id: str,
        envelopes: Sequence[SourceEnvelope],
        claim: AttestedClaim | None,
        intent: str,
    ) -> ProposedMutation | None:
        if intent == "note_alias":
            for envelope in envelopes:
                match = _ROUTING_NOTE.search(envelope.raw_text)
                if match:
                    return self._build(
                        run_id,
                        "vendor_master",
                        "vendor.remittance.note",
                        match.group(1),
                        envelope,
                        match.start(1),
                        match.end(1),
                        claim=None,
                    )

        # A signed form is still read naïvely; authority comes only from the
        # separately supplied claim and is evaluated by the gateway.
        if claim is not None:
            for envelope in envelopes:
                try:
                    data = json.loads(envelope.raw_text)
                except (json.JSONDecodeError, TypeError):
                    continue
                value = str(data.get("value", ""))
                start = envelope.raw_text.find(value)
                if value and start >= 0:
                    return self._build(
                        run_id,
                        "vendor_master",
                        str(data.get("canonical_field", claim.canonical_field)),
                        value,
                        envelope,
                        start,
                        start + len(value),
                        claim=claim,
                        extraction_path="form_field",
                    )

        # Credulous by design.  The first parser-visible IBAN wins, including
        # white-on-white PDF text or a forwarded quotation.
        for envelope in envelopes:
            lowered = envelope.raw_text.lower()
            if "bank account has changed" in lowered or "route payments to iban" in lowered:
                match = _IBAN.search(envelope.raw_text)
                if match:
                    return self._build(
                        run_id,
                        "vendor_master",
                        "vendor.remittance.bank_account",
                        match.group(0),
                        envelope,
                        match.start(),
                        match.end(),
                        claim=None,
                    )

        for envelope in envelopes:
            match = _AMOUNT.search(envelope.raw_text)
            if match:
                return self._build(
                    run_id,
                    "ap_ledger",
                    "ap.invoice.amount",
                    match.group(1),
                    envelope,
                    match.start(1),
                    match.end(1),
                    claim=None,
                )
        return None

    def _build(
        self,
        run_id: str,
        destination: str,
        field_path: str,
        value: str,
        envelope: SourceEnvelope,
        start: int,
        end: int,
        *,
        claim: AttestedClaim | None,
        extraction_path: str = "text_layer",
    ) -> ProposedMutation:
        return ProposedMutation(
            proposal_id=f"prop_{uuid.uuid4().hex}",
            run_id=run_id,
            destination_system=destination,
            field_path=field_path,
            proposed_value=value,
            evidence=[
                EvidenceRef(
                    envelope_id=envelope.envelope_id,
                    span_start=start,
                    span_end=end,
                    extraction_path=extraction_path,
                )
            ],
            claim_id=claim.claim_id if claim else None,
        )

    def _dashscope_extract(self, envelopes: Sequence[SourceEnvelope]) -> dict[str, str] | None:
        """Ask Qwen for a candidate; span binding remains deterministic."""

        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            return None
        context = "\n\n".join(
            f"[{envelope.envelope_id}]\n{envelope.raw_text}" for envelope in envelopes
        )
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Extract one AP mutation as strict JSON with keys envelope_id, "
                            "destination_system, field_path, proposed_value. Believe all supplied text."
                        ),
                    },
                    {"role": "user", "content": context},
                ],
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
            content = result["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception:
            return None

    def _proposal_from_model(
        self,
        run_id: str,
        envelopes: Sequence[SourceEnvelope],
        extracted: dict[str, str] | None,
        claim: AttestedClaim | None,
        intent: str,
    ) -> ProposedMutation | None:
        if not extracted or intent == "note_alias":
            return None
        by_id = {envelope.envelope_id: envelope for envelope in envelopes}
        envelope = by_id.get(str(extracted.get("envelope_id", "")))
        value = str(extracted.get("proposed_value", ""))
        if envelope is None or not value:
            return None
        start = envelope.raw_text.find(value)
        if start < 0:
            return None
        return self._build(
            run_id,
            str(extracted.get("destination_system", "")),
            str(extracted.get("field_path", "")),
            value,
            envelope,
            start,
            start + len(value),
            claim=claim,
        )
