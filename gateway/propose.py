"""Evaluate-then-commit as one operation, returning only external shapes.

This module exists so callers that face the agent (the MCP layer) never touch
evaluate()'s mode parameter or the DecisionRecord. The record is needed here —
broker.commit() requires it — but it stops here. What leaves this function is
an ExternalResponse and, on ALLOW, the downstream effect string. mcp/ imports
this and nothing else from the decision path; a source-scan test enforces it.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from schemas import (
    AttestedClaim,
    ContextManifest,
    Decision,
    ExternalResponse,
    ProposedMutation,
    SourceEnvelope,
)

from gateway import broker as broker_module
from gateway.broker import Broker, BrokerRefused
from gateway.evaluate import evaluate
from gateway.store import Store

_MESSAGES = {
    Decision.ALLOW: "Request accepted.",
    Decision.DENY: "Request denied.",
    Decision.QUARANTINE: "Request pending review.",
    Decision.REQUIRE_APPROVAL: "Request pending review.",
}


def propose_and_commit(
    proposal: ProposedMutation,
    manifest: ContextManifest,
    envelopes: Mapping[str, SourceEnvelope] | Iterable[SourceEnvelope],
    *,
    claim: AttestedClaim | None = None,
    broker: Broker | None = None,
    store: Store | None = None,
) -> tuple[ExternalResponse, str | None]:
    """One call: gate, then execute if allowed. The agent holds no credentials,
    so splitting evaluate and commit across two agent-visible calls would be a
    TOCTOU window with no upside.

    Returns (external_response, downstream_effect). The effect is None unless
    the decision was ALLOW and the broker executed.
    """
    detail = evaluate(proposal, manifest, envelopes, claim=claim, mode="diagnostic", store=store)
    decision = detail.decision
    effect: str | None = None

    if decision == Decision.ALLOW:
        try:
            if broker is not None:
                effect = broker.commit(detail.record, proposal, manifest, envelopes, claim=claim)
            else:
                effect = broker_module.commit(detail.record, proposal, manifest, envelopes, claim=claim)
        except BrokerRefused:
            # The gate said yes but the broker would not execute (revalidation
            # flipped, endpoint unknown, in-flight duplicate). No effect
            # occurred; externally this is a denial and nothing more.
            decision, effect = Decision.DENY, None

    return (
        ExternalResponse(
            proposal_id=proposal.proposal_id,
            decision=decision,
            message=_MESSAGES[decision],
        ),
        effect,
    )
