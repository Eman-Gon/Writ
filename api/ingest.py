"""POST /v1/ingest — envelope creation at the security boundary.

pipeline_credential only. Reuses the connector's content inspection: the
declared type is advisory, the bytes decide. One artifact yields many
envelopes when nested (email + attachment + archive members); every envelope's
attributes are assigned independently — children inherit nothing, and this
module never copies an attribute parent → child.

Hashes are pinned here, at ingestion. Evaluation later verifies against the
pin; nothing ever recomputes a hash from re-fetched bytes.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from connector.ingest import ingest as connector_ingest
from gateway.store import Store
from wire import IngestedEnvelope, IngestRequest, IngestResponse


def make_ingest_router(store: Store, require_pipeline) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_pipeline)])

    @router.post("/v1/ingest", response_model=IngestResponse)
    async def ingest_document(
        file: UploadFile = File(...),
        meta: str = Form(...),
    ) -> IngestResponse:
        try:
            request = IngestRequest.model_validate_json(meta)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=422, detail="empty file")

        envelopes = connector_ingest(
            raw,
            request.declared_type,
            request.ingestion_channel,
            request.origin_principal,
            request.authentication_assurance,
        )

        # Pin every hash now. pin_envelope() is first-write-wins, so a
        # re-ingest of identical bytes re-pins harmlessly; different bytes
        # under a colliding id would fail verification later, not silently win.
        for envelope in envelopes:
            store.pin_envelope(envelope.envelope_id, envelope.artifact_hash)

        root = envelopes[0]
        root_document_id = f"doc_{uuid.uuid4().hex[:20]}"
        received_at = request.received_at.isoformat()
        queue = request.ingestion_channel.value

        # Every envelope is addressable as a document so an agent can fetch a
        # child (the attachment) without the parent, and vice versa. Children
        # resolve to the same root_document_id for task-allowlist purposes:
        # allowlisting the artifact covers what is inside it.
        for envelope in envelopes:
            document_id = root_document_id if envelope is root else f"doc_{uuid.uuid4().hex[:20]}"
            store.add_document(
                document_id,
                envelope,
                root_document_id=root_document_id,
                queue=queue,
                sender_display=envelope.origin_principal,
                received_at=received_at,
            )

        return IngestResponse(
            document_id=root_document_id,
            envelopes=[
                IngestedEnvelope(
                    envelope_id=envelope.envelope_id,
                    artifact_type=envelope.artifact_type,
                    parent_envelope_id=envelope.parent_envelope_id,
                )
                for envelope in envelopes
            ],
            root_envelope_id=root.envelope_id,
            parse_paths_agree=all(envelope.parse_paths_agree for envelope in envelopes),
        )

    return router
