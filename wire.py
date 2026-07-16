"""
Writ — wire contract.

CONTRACT FILE. Same rules as schemas.py and normalize.py: both workstreams
import it, neither edits it unilaterally. If it needs to change, change it here
once and tell the other side.

This exists because a wire mismatch fails as a 422 at midnight rather than a
TypeError at import. The broker-signature incident cost an evening; HTTP and
MCP schemas are worse.

TWO SURFACES, TWO IDENTITIES
----------------------------
  HTTP  /v1/*        auth: pipeline_credential   (the customer's orchestrator)
  MCP   writ_*       auth: task_token            (the customer's agent)

The agent has NO pipeline_credential. If it can reach /v1/ingest it can assign
itself attestation_type=SYSTEM_SIGNED and the entire product is void.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from schemas import (
    ArtifactType,
    AuthenticationAssurance,
    Decision,
    EvidenceRef,
    IngestionChannel,
)

WIRE_VERSION = "1.0.0"


# ===========================================================================
# HTTP  --  pipeline_credential only
# ===========================================================================


class IngestRequest(BaseModel):
    """POST /v1/ingest  (multipart: `file` + this as `meta`)

    `declared_type` is what the caller CLAIMS. The connector determines the
    real type by content inspection; disagreement yields ArtifactType.AMBIGUOUS.
    The caller's claim never wins.
    """

    declared_type: str = Field(description="Caller's claim, e.g. 'application/pdf'. Advisory only.")
    ingestion_channel: IngestionChannel
    origin_principal: str = Field(description="Who transmitted it, e.g. 'vendor_123'.")
    authentication_assurance: AuthenticationAssurance
    received_at: datetime


class IngestedEnvelope(BaseModel):
    envelope_id: str
    artifact_type: ArtifactType
    parent_envelope_id: str | None = None


class IngestResponse(BaseModel):
    """envelope_ids is PLURAL: one artifact yields many envelopes when nested
    (email + attachment + archive members). Children inherit nothing.

    parse_paths_agree=False means this artifact will fail closed on high-impact
    fields. Surface it to the customer now; do not let it become a silent
    denial three steps later.
    """

    document_id: str
    envelopes: list[IngestedEnvelope]
    root_envelope_id: str
    parse_paths_agree: bool


class TaskRequest(BaseModel):
    """POST /v1/tasks

    Task boundaries are declared by the orchestrator, NEVER by the agent. An
    agent that could mint a task could shed taint by starting a fresh one --
    laundering, self-served. But a manifest scoped to the whole connection
    recreates FIDES's over-tainting problem. One task ~= one invoice.
    """

    task_ref: str = Field(description="Customer's reference, e.g. 'invoice-INV-882'.")
    document_ids: list[str] = Field(description="Documents this task may fetch. Fetches outside this set are refused.")
    ttl_seconds: int = 3600


class TaskResponse(BaseModel):
    task_id: str
    task_token: str = Field(description="Passed into the agent's MCP client config for this run.")
    expires_at: datetime


class ApprovalDecisionRequest(BaseModel):
    """POST /v1/approvals/{approval_id}  (authenticated human, not the agent)

    Bound to mutation_digest. Any change to the proposal invalidates it.
    Initiator cannot approve. CRITICAL requires two distinct approvers.
    """

    approve: bool
    mutation_digest: str = Field(description="Must match the pending proposal's digest exactly.")
    reason: str


class ApprovalDecisionResponse(BaseModel):
    approval_id: str
    proposal_id: str
    status: str = Field(description="'pending_second_approver' | 'approved' | 'denied' | 'expired' | 'digest_mismatch'")
    approvers: list[str]


# ===========================================================================
# MCP  --  task_token only.  Four tools. Nothing else.
# ===========================================================================


class DocumentRef(BaseModel):
    """Returned by writ_list_documents. NO CONTENT.

    Listing is not fetching and does not enter the manifest.
    """

    document_id: str
    artifact_type: ArtifactType
    received_at: datetime
    sender_display: str


class ListDocumentsParams(BaseModel):
    queue: str = Field(
        default="",
        description="Filter by ingestion queue, e.g. 'inbound_email'. Empty = every queue in task scope.",
    )


class ListDocumentsResult(BaseModel):
    documents: list[DocumentRef]


class FetchDocumentParams(BaseModel):
    document_id: str


class FetchDocumentResult(BaseModel):
    """THIS CALL IS THE MANIFEST ENTRY.

    The server records (task_id, envelope_id) BEFORE returning. That is how
    context lineage is captured with zero framework hooks: the agent cannot
    read a document without telling us it read it.

    `text` is envelope.raw_text -- already normalize_text()'d -- so the offsets
    the agent sees are the offsets spans index into. Do not re-normalize.

    Note what is ABSENT: no trust, no authority, no attestation_type. The agent
    has no business reasoning about those, and returning them invites it to try.
    artifact_type is returned only so a well-behaved agent can self-limit.
    """

    envelope_id: str
    artifact_type: ArtifactType
    text: str


class ProposeMutationParams(BaseModel):
    """`evidence` is the agent's NOMINATION, not evidence. The server verifies
    each ref against the hash-pinned envelope: does normalize_field(value)
    equal normalize_field(raw_text[span_start:span_end])?

    A proposal with no evidence classifies UNVERIFIED -> denied for CRITICAL.
    That is the bounded failure mode when an agent reads out-of-band.
    """

    destination_system: str
    field_path: str = Field(description="As the agent names it. Resolved via registry; never trusted.")
    proposed_value: str
    evidence: list[EvidenceRef] = Field(default_factory=list)


class ProposeMutationResult(BaseModel):
    """Evaluate and commit are ONE call. The agent holds no credentials, so a
    separate commit primitive would be a TOCTOU window with no upside.

    OPAQUE ON DENIAL. No rule id, no control name, no lineage, no support
    quality, no catch_type. DiagnosticResponse must be structurally unreachable
    from the MCP layer -- not a flag that defaults off. Unreachable.
    """

    proposal_id: str
    decision: Decision
    message: str = Field(description="Human-readable. On denial: opaque. 'Request denied.' and nothing more.")
    effect: str | None = Field(default=None, description="downstream_effect, only when decision == ALLOW.")


class CheckStatusParams(BaseModel):
    proposal_id: str


class CheckStatusResult(BaseModel):
    proposal_id: str
    decision: Decision
    effect: str | None = None


# ---------------------------------------------------------------------------
# MCP tool names -- import these; do not retype the strings on either side.
# ---------------------------------------------------------------------------

TOOL_LIST_DOCUMENTS = "writ_list_documents"
TOOL_FETCH_DOCUMENT = "writ_fetch_document"
TOOL_PROPOSE_MUTATION = "writ_propose_mutation"
TOOL_CHECK_STATUS = "writ_check_status"

MCP_TOOLS = (
    TOOL_LIST_DOCUMENTS,
    TOOL_FETCH_DOCUMENT,
    TOOL_PROPOSE_MUTATION,
    TOOL_CHECK_STATUS,
)

# Deliberately absent, and each for a reason:
#   writ_ingest        -> agent could assign its own provenance
#   writ_begin_task    -> agent could shed taint by starting fresh
#   writ_approve       -> agent could authorize itself
#   writ_read_policy   -> no reason for the agent to know
#   writ_check_mutation (dry-run) -> convenient for planning, and an oracle.
#                         A compromised agent probes the policy boundary for
#                         free. Revisit only with a rate limit and an argument.