"""Gateway decisions driven by REAL connector-emitted envelopes.

Envelopes come from fixtures/envelopes.py (the connector's actual output) and
proposals from the real agent, so content inspection, normalization, hashes,
spans, and child lineage are exercised on the same seam as the demo. The
hand-built envelopes in test_stateguard.py stay as fast unit coverage; span
misalignment between workstreams shows up HERE.
"""

from __future__ import annotations

import pytest

from agent.ap_agent import APAgent
from fixtures.envelopes import (
    CLEAN_INVOICE_ENVELOPE,
    FORWARDED_EMAIL_ENVELOPES,
    POISONED_INVOICE_ENVELOPE,
    QR_AMOUNT_INVOICE_ENVELOPE,
    VENDOR_CHANGE_CLAIM,
    VENDOR_CHANGE_FORM_ENVELOPE,
)
from gateway.broker import Broker
from gateway.evaluate import evaluate
from gateway.store import Store
from mock.erp import MockERP
from schemas import ArtifactType, Decision, ProposedMutation, SupportQuality

AGENT = APAgent()


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path / "stateguard.db"))


def _decide(envelopes, store, claim=None, intent=None):
    kwargs = {"claim": claim} if claim is not None else {}
    if intent is not None:
        kwargs["intent"] = intent
    run = AGENT.run(list(envelopes), **kwargs)
    assert len(run.proposals) == 1
    proposal = run.proposals[0]
    record = evaluate(
        proposal, run.manifest, list(envelopes), claim=claim, mode="diagnostic", store=store
    ).record
    return proposal, run, record


def test_act1_clean_invoice_allows(store):
    _, _, d = _decide([CLEAN_INVOICE_ENVELOPE], store)
    assert d.decision == Decision.ALLOW
    assert d.support_quality == SupportQuality.DIRECT_VERIFIED


def test_act2_poisoned_invoice_denied_defense_in_depth(store):
    _, _, d = _decide([POISONED_INVOICE_ENVELOPE], store)
    assert d.decision == Decision.DENY
    assert d.catch_type == "defense_in_depth"


def test_act3_forwarded_email_denied_as_derived(store):
    email, attachment = FORWARDED_EMAIL_ENVELOPES
    proposal, _, d = _decide(FORWARDED_EMAIL_ENVELOPES, store)
    # The agent cited only the admin's email...
    assert {ref.envelope_id for ref in proposal.evidence} == {email.envelope_id}
    # ...and the manifest still caught the invoice ancestor.
    assert d.decision == Decision.DENY
    assert d.support_quality == SupportQuality.DERIVED
    assert d.least_trusted_ancestor == attachment.envelope_id


def test_act4_attested_form_allows_and_commits(store):
    proposal, run, d = _decide(
        [VENDOR_CHANGE_FORM_ENVELOPE], store, claim=VENDOR_CHANGE_CLAIM
    )
    assert d.decision == Decision.ALLOW
    assert d.support_quality == SupportQuality.STRUCTURED_ATTESTED

    erp = MockERP()

    def executor(mutation, record):
        return erp.apply(mutation.destination_system, record.canonical_field, mutation.proposed_value)

    broker = Broker({"vendor_master": executor}, store=store)
    effect = broker.commit(
        d,
        proposal,
        manifest=run.manifest,
        envelopes=[VENDOR_CHANGE_FORM_ENVELOPE],
        claim=VENDOR_CHANGE_CLAIM,
    )
    assert effect == "bank_account_updated"
    assert erp.vendors["vendor_123"].bank_account == proposal.proposed_value

    folded = store.get_decision(d.decision_id)
    assert folded.executed and folded.downstream_effect == "bank_account_updated"


def test_act5_note_alias_denied_as_payment_routing(store):
    _, _, d = _decide(FORWARDED_EMAIL_ENVELOPES, store, intent="note_alias")
    assert d.decision == Decision.DENY
    assert d.canonical_field == "vendor.remittance.note"
    assert d.business_effect and d.business_effect.value == "payment_routing"


# -- utility probe: the QR-amount invoice. Recorded behavior, per spec. --------


def test_qr_amount_invoice_recorded_behavior(store):
    """Legitimate invoice whose amount exists only in a QR code.

    Recorded outcome (2026-07-16): the connector flags the artifact AMBIGUOUS
    with parse_paths_agree=False (QR payload disagrees with the text layer) and
    the naive agent extracts nothing, so no mutation is even proposed. If a
    mutation DOES cite it, the QR value is absent from raw_text, the span fails,
    and a LOW-sensitivity amount lands in QUARANTINE -- held for a human, not
    silently posted and not hard-denied. A gate that blocks real invoices isn't
    a product; quarantine is the honest middle.
    """
    env = QR_AMOUNT_INVOICE_ENVELOPE
    assert env.artifact_type == ArtifactType.AMBIGUOUS
    assert env.parse_paths_agree is False

    run = AGENT.run([env])
    assert run.proposals == []

    proposal = ProposedMutation(
        proposal_id="p_qr",
        run_id=run.manifest.run_id,
        destination_system="ap_ledger",
        field_path="invoice.amount",
        proposed_value="4200.00",  # what a QR-reading agent would claim
    )
    d = evaluate(proposal, run.manifest, [env], mode="diagnostic", store=store).record
    assert d.decision == Decision.QUARANTINE
    assert d.support_quality == SupportQuality.UNVERIFIED
