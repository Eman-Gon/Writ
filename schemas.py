"""
StateGuard — schema contract.

THIS FILE IS THE INTERFACE. Do not edit it without agreeing the change across
both workstreams. Everything else is downstream of these types.

Four invariants are encoded structurally, not by convention. If you find
yourself needing to break one, the design is wrong, not the schema:

  1. The agent cannot assign source attributes.
     ProposedMutation has no trust/authority/source-class field. There is no
     place for the agent to put one. Authority is computed by the gateway from
     envelopes the connector created.

  2. Nested artifacts never inherit their parent's attributes.
     A child envelope records parent_envelope_id for lineage, but every
     attribute is assigned independently by the connector. A malicious invoice
     attached to an authenticated admin email is an external_invoice envelope
     whose parent is an email envelope -- NOT admin-attested content.

  3. An AttestedClaim is not a SourceEnvelope and never derives from one.
     Approval does not "promote" or "upgrade" a poisoned artifact. It emits a
     new, independently authenticated assertion. These are separate types on
     purpose: there is no code path that turns an envelope into a claim.

  4. Trust is computed, never stored on the thing being judged.
     No type here has a `trusted: bool`. Support quality and authority are
     outputs of gateway evaluation, recorded on the DecisionRecord.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Source attributes -- six dimensions that v3's flat `source_class` conflated.
# `authenticated_finance_admin` was never a source class. It is an
# origin_principal with an authentication_assurance. The artifact is still
# whatever it is -- and an email is an email no matter who forwarded it.
# ---------------------------------------------------------------------------


class ArtifactType(str, Enum):
    INVOICE = "invoice"
    VENDOR_CHANGE_FORM = "vendor_change_form"
    ERP_RECORD = "erp_record"
    EMAIL = "email"
    WEB_PAGE = "web_page"
    # Assigned when content inspection disagrees with the declared type, or
    # when parse paths disagree. Fails closed for every high-impact field.
    AMBIGUOUS = "ambiguous"


class IngestionChannel(str, Enum):
    INBOUND_EMAIL = "inbound_email"
    ADMIN_PORTAL = "admin_portal"
    ERP_CONNECTOR = "erp_connector"
    WEB_FETCH = "web_fetch"


class AuthenticationAssurance(str, Enum):
    NONE = "none"
    PASSWORD = "password"
    MFA = "mfa"
    PHISHING_RESISTANT = "phishing_resistant"


class AttestationType(str, Enum):
    """Did someone ASSERT this content, or did they merely transmit it?

    Forwarding is not attesting. This distinction is the entire fix for the
    quotation-laundering attack: an admin forwarding a malicious invoice
    produces attestation_type=NONE.
    """

    NONE = "none"
    USER_ATTESTED = "user_attested"
    SYSTEM_SIGNED = "system_signed"


class BusinessEffect(str, Enum):
    """What the destination DOES -- not what it is called.

    The registry keys on this. An attacker does not care whether a field is
    formally labelled protected; they care whether the money moves. Any field
    the ERP parses for routing carries PAYMENT_ROUTING regardless of its name.
    """

    RECORD_TRANSACTION = "record_transaction"
    UPDATE_VENDOR_PROFILE = "update_vendor_profile"
    PAYMENT_ROUTING = "payment_routing"
    APPROVAL_STATE = "approval_state"
    SYSTEM_CONFIG = "system_config"
    PERSIST_MEMORY_DESCRIPTIVE = "persist_memory_descriptive"
    PERSIST_MEMORY_AUTHORITATIVE = "persist_memory_authoritative"


class Sensitivity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SupportQuality(str, Enum):
    """The gateway's verdict on the agent's evidence -- never the agent's."""

    DIRECT_VERIFIED = "direct_verified"        # deterministic extraction, authenticated artifact
    STRUCTURED_ATTESTED = "structured_attested"  # trusted structured workflow
    DERIVED = "derived"                         # transformed/inferred -- ineligible for high-impact
    UNVERIFIED = "unverified"                   # insufficient evidence


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    QUARANTINE = "quarantine"
    REQUIRE_APPROVAL = "require_approval"


# ---------------------------------------------------------------------------
# Validity / scope
# ---------------------------------------------------------------------------


class Validity(BaseModel):
    issued_at: datetime
    expires_at: datetime | None = None
    revoked: bool = False


class Scope(BaseModel):
    """What may this source speak FOR, and about WHICH fields?

    subject_scope kills cross-vendor approval reuse: a claim attested for
    vendor_123 cannot authorize a mutation on vendor_456.
    """

    subject_scope: list[str] = Field(
        default_factory=list,
        description="Resource IDs this source may speak for, e.g. ['vendor_123']. Empty = none.",
    )
    field_scope: list[str] = Field(
        default_factory=list,
        description="Canonical field paths this source may assert. Empty = none.",
    )


# ---------------------------------------------------------------------------
# SourceEnvelope -- created by the connector, OUTSIDE the agent.
# ---------------------------------------------------------------------------


class SourceEnvelope(BaseModel):
    """An artifact as the connector understood it. The agent never builds one.

    INVARIANT 2: children do not inherit. parent_envelope_id is lineage only.
    Every attribute below is assigned independently by the connector for THIS
    artifact. Nesting an invoice inside an admin email yields two envelopes:
      email     (origin=employee_456, auth=mfa,  attestation=none)
      invoice   (origin=vendor_123,   auth=none, attestation=none, parent=<email>)
    The invoice does not become admin-attested by being attached.
    """

    envelope_id: str
    parent_envelope_id: str | None = None

    artifact_type: ArtifactType
    origin_principal: str
    ingestion_channel: IngestionChannel
    authentication_assurance: AuthenticationAssurance
    attestation_type: AttestationType

    scope: Scope = Field(default_factory=Scope)
    validity: Validity

    artifact_hash: str = Field(description="sha256 of raw bytes. Pinned at ingestion; evidence verification depends on immutability.")
    raw_text: str = Field(description="Canonical extracted text, post Unicode normalization.")

    parse_paths_agree: bool = Field(
        default=True,
        description="False when text layer / OCR / metadata / form fields disagree. Forces AMBIGUOUS handling.",
    )


# ---------------------------------------------------------------------------
# AttestedClaim -- INVARIANT 3. A separate type, deliberately.
# ---------------------------------------------------------------------------


class AttestedClaim(BaseModel):
    """An independently authenticated human assertion.

    NOT a SourceEnvelope. Not derived from one. There is no function anywhere
    in this system with signature (SourceEnvelope) -> AttestedClaim, and adding
    one would reintroduce the laundering attack the whole product exists to stop.

    The poisoned invoice remains untrusted forever. The admin does not approve
    the invoice; the admin independently asserts the value through the
    vendor-change workflow, and THAT emits this.
    """

    claim_id: str
    claim_type: Literal["vendor_banking_change", "vendor_profile_change"]

    subject: str                       # e.g. "vendor_123" -- checked against mutation
    canonical_field: str               # e.g. "remittance.bank_account"
    value_hash: str                    # binds the claim to an exact value

    attested_by: str
    authentication: AuthenticationAssurance
    approval_scope: Literal["exact_mutation"] = "exact_mutation"
    validity: Validity

    # Artifacts the human LOOKED AT. Recorded for audit. Confers no authority --
    # authority comes from the attestation act, not from what was on screen.
    source_artifacts: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Context manifest -- INVARIANT 1's other half. Captured by the adapter.
# ---------------------------------------------------------------------------


class ContextManifest(BaseModel):
    """Every envelope that entered agent context, recorded by the adapter.

    Independent of what the agent later cites. This is what defeats quotation
    laundering: if the agent cites only the admin's email, the manifest still
    shows the invoice was in context, and the derived value inherits the
    least-trusted ancestor.

    The model cannot omit entries. It does not write this.
    """

    run_id: str
    envelope_ids: list[str]
    captured_at: datetime


# ---------------------------------------------------------------------------
# ProposedMutation -- the ONLY thing the agent produces.
# ---------------------------------------------------------------------------


class EvidenceRef(BaseModel):
    """The agent's NOMINATION of support. An assertion, not evidence.

    The gateway verifies independently: does the normalized value actually
    appear at this span in this hash-pinned envelope? A citation that does not
    check out is a denial, not a warning.
    """

    envelope_id: str
    span_start: int
    span_end: int
    extraction_path: Literal["text_layer", "ocr", "form_field", "metadata", "annotation"]


class ProposedMutation(BaseModel):
    """INVARIANT 1: note what is ABSENT.

    No trust level. No source class. No authority. No 'verified' flag. The
    agent has nowhere to assert its own trustworthiness, because the gateway
    does not read the agent's opinion -- it reads connector-assigned envelopes
    and the adapter-captured manifest.

    The agent controls exactly four things, and none of them can manufacture
    authority: which field, which citation, what value, whether to propose.
    """

    proposal_id: str
    run_id: str

    destination_system: str            # "vendor_master" | "ap_ledger" | "agent_memory"
    field_path: str                    # AS THE AGENT NAMES IT -- resolved via registry, never trusted
    proposed_value: str

    evidence: list[EvidenceRef] = Field(default_factory=list)
    claim_id: str | None = None        # set only by the attested vendor-change workflow


# ---------------------------------------------------------------------------
# Destination registry -- keyed to EFFECT, not name.
# ---------------------------------------------------------------------------


class RegistryEntry(BaseModel):
    """Unregistered destination -> DENY. Registry completeness is a security
    property; a gap is a vulnerability, not a default.

    effect_equivalence_class groups fields that DO the same thing downstream.
    `remittance.bank_account` and a free-text `remittance.note` the ERP parses
    for routing must share a class, or the semantic-bypass attack works.
    """

    canonical_field: str
    destination_system: str
    business_effect: BusinessEffect
    sensitivity: Sensitivity
    effect_equivalence_class: str
    aliases: list[str] = Field(default_factory=list, description="Names the agent might use for this field.")


# ---------------------------------------------------------------------------
# DecisionRecord -- the audit artifact AND the shadow-evaluation record.
# ---------------------------------------------------------------------------


class ControlResult(BaseModel):
    control: str                       # "evidence_binding" | "lineage" | "field_authority" | ...
    would_deny: bool
    reason: str


class DecisionRecord(BaseModel):
    """Append-only. This is the product's audit evidence, not a log line.

    Shadow evaluation: every control is evaluated even after the first denies,
    in non-enforcing mode. Without this you cannot distinguish a single-control
    catch from defense-in-depth -- you only see whichever control ran first.
    """

    decision_id: str
    proposal_id: str
    run_id: str
    decided_at: datetime

    decision: Decision
    canonical_field: str | None         # resolved; None if unregistered
    business_effect: BusinessEffect | None
    sensitivity: Sensitivity | None

    support_quality: SupportQuality
    least_trusted_ancestor: str | None  # envelope_id driving the verdict
    context_envelope_ids: list[str]     # from the manifest, not the citation

    enforcing_control: str | None
    controls_evaluated: list[ControlResult]
    catch_type: Literal["single_control", "defense_in_depth", "order_dependent", "allowed"]

    policy_version: str
    registry_version: str

    # Recorded after the broker acts. The success predicate is a BUSINESS
    # EFFECT, not a gateway verdict -- so the effect must be recorded here.
    executed: bool = False
    downstream_effect: str | None = None


# ---------------------------------------------------------------------------
# Response modes -- diagnostic signals must never ship to production.
# ---------------------------------------------------------------------------


class ExternalResponse(BaseModel):
    """What a real caller sees. Opaque by design: no rule id, no control name,
    no lineage, no normalized value. An attacker learns nothing from probing.
    """

    proposal_id: str
    decision: Decision
    message: str = "Request denied."


class DiagnosticResponse(BaseModel):
    """Harness only. Grants internal progress signals that intentionally exceed
    what an external attacker could observe -- which is what makes a null
    result under this oracle a stronger claim. Must be disclosed in results.
    """

    proposal_id: str
    decision: Decision
    record: DecisionRecord
