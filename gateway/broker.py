"""Sole credential holder. commit() is the only code path that touches a
destination system.

The executors passed to configure()/Broker() own the destination credentials
(mock ERP today). Nothing else in the repo may import ERP or payment client
config -- if the agent module can reach the destination, the design has failed.
"""

from __future__ import annotations

from typing import Callable, Iterable, Mapping

from schemas import (
    AttestedClaim,
    ContextManifest,
    Decision,
    DecisionRecord,
    ProposedMutation,
    SourceEnvelope,
)

from gateway.evaluate import evaluate
from gateway.store import Store, get_store

# An executor performs the mutation against its destination and returns the
# concrete downstream_effect string, e.g. "bank_account_updated".
Executor = Callable[[ProposedMutation, DecisionRecord], str]


class BrokerRefused(Exception):
    """Raised instead of executing. No business effect occurred."""


class Broker:
    def __init__(self, endpoints: Mapping[str, Executor], store: Store | None = None):
        self._endpoints = dict(endpoints)
        self._store = store or get_store()

    def commit(
        self,
        decision_record: DecisionRecord,
        mutation: ProposedMutation,
        manifest: ContextManifest,
        envelopes: Mapping[str, SourceEnvelope] | Iterable[SourceEnvelope],
        *,
        claim: AttestedClaim | None = None,
    ) -> str:
        """At-most-once business effect keyed on proposal_id. A duplicate is a
        no-op returning the prior effect. Not exactly-once: a commit whose
        outcome was lost mid-flight refuses rather than re-executing.

        The caller supplies manifest and envelopes because commit-time
        revalidation re-runs evaluate() against them immediately before
        executing.
        """
        store = self._store

        prior = store.get_commit(mutation.proposal_id)
        if prior is not None:
            if prior["status"] == "done":
                return prior["downstream_effect"]
            raise BrokerRefused(
                f"proposal {mutation.proposal_id!r} has a commit of unknown outcome; "
                "refusing to re-execute (at-most-once)"
            )

        if decision_record.decision != Decision.ALLOW:
            raise BrokerRefused(f"decision is {decision_record.decision.value!r}, not allow")
        if decision_record.proposal_id != mutation.proposal_id:
            raise BrokerRefused("decision record is for a different proposal")

        persisted = store.get_decision(decision_record.decision_id)
        if persisted is None:
            raise BrokerRefused("no persisted DecisionRecord; the gateway never saw this")
        if persisted.decision != Decision.ALLOW or persisted.proposal_id != mutation.proposal_id:
            raise BrokerRefused("persisted DecisionRecord does not authorize this mutation")
        if persisted.canonical_field is None:
            raise BrokerRefused("persisted DecisionRecord has no resolved field")

        executor = self._endpoints.get(mutation.destination_system)
        if executor is None:
            raise BrokerRefused(f"unknown endpoint {mutation.destination_system!r}")

        if not store.claim_commit(mutation.proposal_id, decision_record.decision_id):
            prior = store.get_commit(mutation.proposal_id)
            if prior is not None and prior["status"] == "done":
                return prior["downstream_effect"]
            raise BrokerRefused("commit already in flight for this proposal")

        # Commit-time revalidation guards TOCTOU: the context must still pass
        # the gate NOW, against current envelope pins, not at evaluation time.
        recheck = evaluate(
            mutation, manifest, envelopes, claim=claim, mode="diagnostic", store=store
        )
        if recheck.decision != Decision.ALLOW:
            store.release_commit(mutation.proposal_id)
            store.append_effect(decision_record.decision_id, False, None)
            raise BrokerRefused(
                f"commit-time revalidation returned {recheck.decision.value!r}; not executing"
            )

        effect = executor(mutation, persisted)

        # The success predicate is a business effect, not a verdict -- so the
        # effect lands on the record, via follow-up event (append-only).
        store.append_effect(decision_record.decision_id, True, effect)
        store.finish_commit(mutation.proposal_id, effect)
        return effect


_default_broker: Broker | None = None


def configure(endpoints: Mapping[str, Executor], store: Store | None = None) -> Broker:
    """Called once from the composition root (demo.py). Wires the destination
    executors into the broker; this is the only place credentials enter."""
    global _default_broker
    _default_broker = Broker(endpoints, store)
    return _default_broker


def commit(
    decision_record: DecisionRecord,
    mutation: ProposedMutation,
    manifest: ContextManifest,
    envelopes: Mapping[str, SourceEnvelope] | Iterable[SourceEnvelope],
    *,
    claim: AttestedClaim | None = None,
) -> str:
    if _default_broker is None:
        raise BrokerRefused("broker not configured; call gateway.broker.configure(...) first")
    return _default_broker.commit(decision_record, mutation, manifest, envelopes, claim=claim)
