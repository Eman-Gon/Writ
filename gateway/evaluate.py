"""The gate. evaluate() is the only path a proposal takes toward a business effect.

Order: resolve destination -> verify evidence binding -> compute least-trusted
ancestor from the MANIFEST (not the citation) -> classify support -> policy ->
shadow-evaluate every control -> persist DecisionRecord -> respond per mode.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Iterable, Mapping

from normalize import UnknownFieldKind, field_kind_for, normalize_field
from schemas import (
    ArtifactType,
    AttestedClaim,
    AttestationType,
    AuthenticationAssurance,
    ContextManifest,
    ControlResult,
    Decision,
    DecisionRecord,
    DiagnosticResponse,
    ExternalResponse,
    ProposedMutation,
    Sensitivity,
    SourceEnvelope,
    SupportQuality,
)

from gateway.policy import SENSITIVITY_RANK, get_policy, validate_claim
from gateway.registry import get_registry
from gateway.store import Store, get_store

_ATTEST_RANK = {
    AttestationType.NONE: 0,
    AttestationType.USER_ATTESTED: 1,
    AttestationType.SYSTEM_SIGNED: 2,
}
_AUTH_RANK = {
    AuthenticationAssurance.NONE: 0,
    AuthenticationAssurance.PASSWORD: 1,
    AuthenticationAssurance.MFA: 2,
    AuthenticationAssurance.PHISHING_RESISTANT: 3,
}

# Which control a policy reason lands on when it enforces.
_ENFORCING_CONTROL = {
    "unregistered_destination": "field_authority",
    "unknown_field_kind": "field_authority",
    "ambiguous_source": "lineage",
    "critical_lineage": "lineage",
    "critical_attestation_missing": "attestation_present",
    "critical_claim_invalid": "claim_validity",
    "high_support_insufficient": "lineage",
    "medium_unverified": "evidence_binding",
    "low_not_direct": "evidence_binding",
}


def value_hash(canonical_field: str, value: str) -> str:
    """sha256 over the field-normalized value. AttestedClaim.value_hash must be
    computed with THIS function or claim binding will never match."""
    normalized = normalize_field(value, field_kind_for(canonical_field))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def evaluate(
    proposal: ProposedMutation,
    manifest: ContextManifest,
    envelopes: Mapping[str, SourceEnvelope] | Iterable[SourceEnvelope],
    *,
    claim: AttestedClaim | None = None,
    mode: str = "external",
    store: Store | None = None,
) -> ExternalResponse | DiagnosticResponse:
    store = store or get_store()
    registry = get_registry()
    policy = get_policy()
    env_index = _index_envelopes(envelopes)

    # 1. Resolve destination. None means DENY, not "unknown, proceed."
    entry = registry.resolve(proposal.destination_system, proposal.field_path)

    kind = None
    kind_missing = False
    if entry is not None:
        try:
            kind = field_kind_for(entry.canonical_field)
        except UnknownFieldKind:
            # Registered destination with no normalization rule: a contract
            # gap, and a gap is a DENY.
            kind_missing = True

    # 2. Verify evidence binding: the agent's citations are nominations, not
    # evidence, until the normalized value matches the hash-pinned span.
    evidence_failures: list[str] = []
    verified_envelopes: list[SourceEnvelope] = []
    for ref in proposal.evidence:
        env = env_index.get(ref.envelope_id)
        if env is None:
            evidence_failures.append(f"{ref.envelope_id}: envelope not provided")
            continue
        if not store.pin_envelope(env.envelope_id, env.artifact_hash):
            evidence_failures.append(f"{ref.envelope_id}: artifact_hash changed since pin")
            continue
        if not (0 <= ref.span_start < ref.span_end <= len(env.raw_text)):
            evidence_failures.append(f"{ref.envelope_id}: span out of bounds")
            continue
        if kind is None:
            evidence_failures.append(f"{ref.envelope_id}: no field kind to normalize under")
            continue
        span_text = env.raw_text[ref.span_start : ref.span_end]
        normalized_value = normalize_field(proposal.proposed_value, kind)
        # Empty normalization is a failure by contract (unparseable date/amount);
        # "" == "" must not count as verified.
        if normalized_value == "" or normalized_value != normalize_field(span_text, kind):
            evidence_failures.append(
                f"{ref.envelope_id}: proposed value does not match span [{ref.span_start}:{ref.span_end}]"
            )
            continue
        verified_envelopes.append(env)

    spans_verified = bool(proposal.evidence) and not evidence_failures

    # 3. Least-trusted ancestor from the MANIFEST, walking parent chains.
    # Citing only the admin's email does not remove the invoice from context.
    context_envs, missing_context = _expand_context(manifest, env_index)
    context_complete = not missing_context and manifest.run_id == proposal.run_id
    least_trusted = min(context_envs.values(), key=_trust_key) if context_envs else None

    # Claim validation. The subject a mutation can be about is bounded by the
    # subject_scope of its verified evidence -- this is what makes a claim for
    # vendor_123 useless against a mutation about vendor_456.
    subject_candidates: set[str] = set()
    for env in verified_envelopes:
        subject_candidates.update(env.scope.subject_scope)

    claim_valid = False
    claim_reason = "no claim presented"
    if entry is not None and not kind_missing and claim is not None:
        claim_valid, claim_reason = validate_claim(
            claim,
            canonical_field=entry.canonical_field,
            value_hash=value_hash(entry.canonical_field, proposal.proposed_value),
            subject_candidates=subject_candidates,
            required_authentication=policy.claim_required_authentication,
        )

    # 4. Support quality -- the gateway's verdict on the evidence, never the agent's.
    cited_ids = {ref.envelope_id for ref in proposal.evidence}
    if evidence_failures or not context_complete:
        support = SupportQuality.UNVERIFIED
    elif claim_valid:
        # A valid claim outranks direct extraction: the attested path must be
        # visibly distinct from a span match.
        support = SupportQuality.STRUCTURED_ATTESTED
    elif spans_verified and set(context_envs) == cited_ids:
        support = SupportQuality.DIRECT_VERIFIED
    elif spans_verified:
        support = SupportQuality.DERIVED
    else:
        support = SupportQuality.UNVERIFIED

    # 5. Policy.
    decision, reason = policy.decide(
        entry, support, least_trusted, claim if claim_valid else None
    )
    if kind_missing and decision == Decision.ALLOW:
        decision, reason = Decision.DENY, "unknown_field_kind"
    if reason == "critical_attestation_missing" and claim is not None:
        reason = "critical_claim_invalid"

    # 6. Shadow evaluation -- every control, no short-circuit. Without this you
    # cannot tell which controls are load-bearing.
    high_plus = entry is not None and SENSITIVITY_RANK[entry.sensitivity] >= SENSITIVITY_RANK[Sensitivity.HIGH]
    critical = entry is not None and entry.sensitivity == Sensitivity.CRITICAL

    field_scope_union: set[str] = set()
    for env in verified_envelopes:
        field_scope_union.update(env.scope.field_scope)

    controls = [
        ControlResult(
            control="evidence_binding",
            would_deny=bool(evidence_failures) or (not proposal.evidence and claim is None),
            reason="; ".join(evidence_failures)
            if evidence_failures
            else ("no evidence and no claim" if not proposal.evidence and claim is None else "all citations verified"),
        ),
        ControlResult(
            control="lineage",
            would_deny=(not context_complete)
            or (
                high_plus
                and (
                    support in (SupportQuality.DERIVED, SupportQuality.UNVERIFIED)
                    or (least_trusted is not None and _is_ambiguous(least_trusted))
                )
            ),
            reason=(
                f"context incomplete: missing {missing_context}"
                if missing_context
                else "manifest run_id does not match proposal"
                if manifest.run_id != proposal.run_id
                else f"least-trusted ancestor {least_trusted.envelope_id if least_trusted else None}, support {support.value}"
            ),
        ),
        ControlResult(
            control="field_authority",
            would_deny=entry is None
            or kind_missing
            or (
                high_plus
                and not claim_valid
                and entry.canonical_field not in field_scope_union
            ),
            reason="unregistered destination"
            if entry is None
            else "no normalization rule for field"
            if kind_missing
            else f"effect {entry.business_effect.value} at {entry.sensitivity.value}",
        ),
        ControlResult(
            control="attestation_present",
            would_deny=critical and claim is None,
            reason="critical field with no attested claim" if critical and claim is None else "not required or present",
        ),
        ControlResult(
            control="subject_scope",
            would_deny=claim is not None and claim.subject not in subject_candidates,
            reason=(
                f"claim subject {claim.subject!r} outside verified evidence scope {sorted(subject_candidates)}"
                if claim is not None and claim.subject not in subject_candidates
                else "in scope or no claim"
            ),
        ),
        ControlResult(
            control="claim_validity",
            would_deny=claim is not None and not claim_valid,
            reason=claim_reason if claim is not None else "no claim to validate",
        ),
    ]

    deny_count = sum(1 for c in controls if c.would_deny)
    if decision == Decision.ALLOW or deny_count == 0:
        catch_type = "allowed"
    elif deny_count == 1:
        catch_type = "single_control"
    else:
        catch_type = "defense_in_depth"

    # 7. Persist, then respond per mode. Diagnostic detail never leaks by default.
    record = DecisionRecord(
        decision_id=uuid.uuid4().hex,
        proposal_id=proposal.proposal_id,
        run_id=proposal.run_id,
        decided_at=datetime.now(timezone.utc),
        decision=decision,
        canonical_field=entry.canonical_field if entry else None,
        business_effect=entry.business_effect if entry else None,
        sensitivity=entry.sensitivity if entry else None,
        support_quality=support,
        least_trusted_ancestor=least_trusted.envelope_id if least_trusted else None,
        context_envelope_ids=sorted(set(manifest.envelope_ids) | set(context_envs)),
        enforcing_control=None if decision == Decision.ALLOW else _ENFORCING_CONTROL.get(reason, reason),
        controls_evaluated=controls,
        catch_type=catch_type,
        policy_version=policy.version,
        registry_version=registry.version,
    )
    store.append_decision(record)

    if mode == "diagnostic":
        return DiagnosticResponse(proposal_id=proposal.proposal_id, decision=decision, record=record)
    return ExternalResponse(
        proposal_id=proposal.proposal_id,
        decision=decision,
        message="Request accepted." if decision == Decision.ALLOW else "Request denied.",
    )


def _index_envelopes(
    envelopes: Mapping[str, SourceEnvelope] | Iterable[SourceEnvelope],
) -> dict[str, SourceEnvelope]:
    if isinstance(envelopes, Mapping):
        return dict(envelopes)
    return {env.envelope_id: env for env in envelopes}


def _expand_context(
    manifest: ContextManifest, env_index: Mapping[str, SourceEnvelope]
) -> tuple[dict[str, SourceEnvelope], list[str]]:
    """Manifest envelopes plus every parent_envelope_id ancestor."""
    seen: dict[str, SourceEnvelope] = {}
    missing: list[str] = []
    stack = list(manifest.envelope_ids)
    while stack:
        envelope_id = stack.pop()
        if envelope_id in seen or envelope_id in missing:
            continue
        env = env_index.get(envelope_id)
        if env is None:
            missing.append(envelope_id)
            continue
        seen[envelope_id] = env
        if env.parent_envelope_id is not None:
            stack.append(env.parent_envelope_id)
    return seen, sorted(missing)


def _is_ambiguous(env: SourceEnvelope) -> bool:
    return env.artifact_type == ArtifactType.AMBIGUOUS or env.parse_paths_agree is False


def _trust_key(env: SourceEnvelope) -> tuple[int, int, int, str]:
    """Lower sorts first = less trusted. Ambiguity dominates, then
    authentication, then attestation; envelope_id makes ordering deterministic."""
    return (
        0 if _is_ambiguous(env) else 1,
        _AUTH_RANK[env.authentication_assurance],
        _ATTEST_RANK[env.attestation_type],
        env.envelope_id,
    )
