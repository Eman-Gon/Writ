"""The authority matrix: source attributes x business effect.

Not a trust scalar. Authority is recomputed per decision from what the
connector assigned and what the gateway verified -- never read from the agent,
never stored on the thing being judged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from schemas import (
    ArtifactType,
    AttestedClaim,
    AuthenticationAssurance,
    Decision,
    RegistryEntry,
    Sensitivity,
    SourceEnvelope,
    SupportQuality,
)

_POLICY_PATH = Path(__file__).parent / "policy.yaml"

SENSITIVITY_RANK = {
    Sensitivity.LOW: 0,
    Sensitivity.MEDIUM: 1,
    Sensitivity.HIGH: 2,
    Sensitivity.CRITICAL: 3,
}

AUTH_RANK = {
    AuthenticationAssurance.NONE: 0,
    AuthenticationAssurance.PASSWORD: 1,
    AuthenticationAssurance.MFA: 2,
    AuthenticationAssurance.PHISHING_RESISTANT: 3,
}


class Policy:
    def __init__(self, path: Path = _POLICY_PATH):
        data = yaml.safe_load(path.read_text())
        self.version: str = str(data["version"])
        self.claim_required_authentication = AuthenticationAssurance(
            data["claim_required_authentication"]
        )
        self.ambiguity_fail_closed_at = Sensitivity(data["ambiguity_fail_closed_at"])

    def decide(
        self,
        entry: RegistryEntry | None,
        support_quality: SupportQuality,
        least_trusted_envelope: SourceEnvelope | None,
        claim: AttestedClaim | None,
    ) -> tuple[Decision, str]:
        """Rules in order, first match wins.

        `claim` must already have been validated by the caller against the
        exact mutation (see validate_claim). Pass None for absent OR invalid --
        this function never re-checks and never trusts an unvalidated claim.
        """
        if entry is None:
            return Decision.DENY, "unregistered_destination"

        rank = SENSITIVITY_RANK[entry.sensitivity]

        if (
            least_trusted_envelope is not None
            and rank >= SENSITIVITY_RANK[self.ambiguity_fail_closed_at]
            and (
                least_trusted_envelope.artifact_type == ArtifactType.AMBIGUOUS
                or least_trusted_envelope.parse_paths_agree is False
            )
        ):
            return Decision.DENY, "ambiguous_source"

        if entry.sensitivity == Sensitivity.CRITICAL:
            # Lineage dominates: a derived or unverified value is denied
            # regardless of any claim. Approval does not launder provenance.
            if support_quality in (SupportQuality.DERIVED, SupportQuality.UNVERIFIED):
                return Decision.DENY, "critical_lineage"
            if claim is None:
                return Decision.DENY, "critical_attestation_missing"
            return Decision.ALLOW, "critical_attested"

        if entry.sensitivity == Sensitivity.HIGH:
            if support_quality in (
                SupportQuality.STRUCTURED_ATTESTED,
                SupportQuality.DIRECT_VERIFIED,
            ):
                return Decision.ALLOW, "high_support_sufficient"
            return Decision.QUARANTINE, "high_support_insufficient"

        if entry.sensitivity == Sensitivity.MEDIUM:
            if support_quality == SupportQuality.UNVERIFIED:
                return Decision.QUARANTINE, "medium_unverified"
            return Decision.ALLOW, "medium_supported"

        if support_quality == SupportQuality.DIRECT_VERIFIED:
            return Decision.ALLOW, "low_direct_verified"
        return Decision.QUARANTINE, "low_not_direct"


def validate_claim(
    claim: AttestedClaim | None,
    *,
    canonical_field: str,
    value_hash: str,
    subject_candidates: set[str],
    required_authentication: AuthenticationAssurance,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Does this claim independently assert THIS exact mutation?

    subject_candidates is the union of scope.subject_scope over the mutation's
    verified evidence envelopes -- the only subjects this proposal can be about.
    An empty set fails every claim: a subject that cannot be bound cannot be
    attested. This is what kills cross-vendor approval reuse.
    """
    if claim is None:
        return False, "no claim presented"
    if claim.canonical_field != canonical_field:
        return False, f"claim covers {claim.canonical_field!r}, not {canonical_field!r}"
    if claim.value_hash != value_hash:
        return False, "claim value_hash does not match the proposed value"
    if AUTH_RANK[claim.authentication] < AUTH_RANK[required_authentication]:
        return False, f"claim authentication {claim.authentication.value!r} below required"
    if claim.validity.revoked:
        return False, "claim revoked"
    now = now or datetime.now(timezone.utc)
    if _aware(claim.validity.issued_at) > now:
        return False, "claim not yet valid"
    expires = claim.validity.expires_at
    if expires is not None and _aware(expires) <= now:
        return False, "claim expired"
    if claim.subject not in subject_candidates:
        return False, (
            f"claim subject {claim.subject!r} is not within the subject scope "
            "of the verified evidence"
        )
    return True, "ok"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


_default: Policy | None = None


def get_policy() -> Policy:
    global _default
    if _default is None:
        _default = Policy()
    return _default
