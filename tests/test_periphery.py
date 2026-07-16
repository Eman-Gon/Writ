from __future__ import annotations

import ast
import unittest
from pathlib import Path

from agent.ap_agent import APAgent
from connector.ingest import ingest, ingest_attested_claim
from fixtures.envelopes import FIXTURE_ENVELOPES, FORWARDED_EMAIL_ENVELOPES
from mock.erp import MockERP
from schemas import ArtifactType, AttestationType, AuthenticationAssurance


FIXTURES = Path(__file__).parents[1] / "fixtures"


class PeripheryTests(unittest.TestCase):
    def test_hidden_text_fools_agent_with_valid_span(self) -> None:
        envelopes = ingest(
            (FIXTURES / "invoice_poisoned.pdf").read_bytes(),
            "application/pdf",
            "inbound_email",
            "vendor_123",
            "none",
        )
        proposal = APAgent().run(envelopes).proposals[0]
        evidence = proposal.evidence[0]
        self.assertEqual(proposal.field_path, "vendor.remittance.bank_account")
        self.assertEqual(
            envelopes[0].raw_text[evidence.span_start : evidence.span_end],
            proposal.proposed_value,
        )

    def test_attachment_inherits_only_lineage_and_email_is_cited(self) -> None:
        envelopes = ingest(
            (FIXTURES / "email_forwarded.eml").read_bytes(),
            "message/rfc822",
            "inbound_email",
            "finance_admin_456",
            "mfa",
        )
        email, invoice = envelopes
        self.assertEqual(invoice.parent_envelope_id, email.envelope_id)
        self.assertEqual(email.authentication_assurance, AuthenticationAssurance.MFA)
        self.assertEqual(invoice.authentication_assurance, AuthenticationAssurance.NONE)
        self.assertEqual(invoice.attestation_type, AttestationType.NONE)
        self.assertEqual(invoice.origin_principal, "vendor_123")
        run = APAgent().run(envelopes)
        self.assertEqual(run.proposals[0].evidence[0].envelope_id, email.envelope_id)
        self.assertEqual(run.manifest.envelope_ids, [email.envelope_id, invoice.envelope_id])

    def test_declared_type_and_unicode_ambiguity_fail_closed(self) -> None:
        clean = (FIXTURES / "invoice_clean.pdf").read_bytes()
        mismatch = ingest(clean, "application/json", "inbound_email", "vendor_123", "none")
        self.assertEqual(mismatch[0].artifact_type, ArtifactType.AMBIGUOUS)
        suspicious = (
            '{"subject":"vendor_123","canonical_field":"vendor.remittance.bank_account",'
            '"value":"ＧB99"}'
        ).encode("utf-8")
        normalized = ingest(suspicious, "application/json", "admin_portal", "admin", "mfa")
        self.assertEqual(normalized[0].artifact_type, ArtifactType.AMBIGUOUS)
        mixed_script = (
            '{"subject":"vendor_123","canonical_field":"vendor.remittance.bank_account",'
            '"value":"pаypal"}'
        ).encode("utf-8")
        mixed = ingest(mixed_script, "application/json", "admin_portal", "admin", "mfa")
        self.assertEqual(mixed[0].artifact_type, ArtifactType.AMBIGUOUS)

    def test_claim_is_created_from_raw_portal_submission(self) -> None:
        raw = (FIXTURES / "vendor_change_form.json").read_bytes()
        auth = {"assurance": "phishing_resistant", "system_signed": True, "user_attested": True}
        claim = ingest_attested_claim(raw, "admin_portal", "finance_admin_456", auth)
        self.assertEqual(claim.subject, "vendor_123")
        with self.assertRaises(ValueError):
            ingest_attested_claim(raw, "inbound_email", "finance_admin_456", auth)

    def test_erp_note_has_real_payment_routing_effect(self) -> None:
        erp = MockERP()
        erp.update_remittance_note("vendor_123", "route payments to IBAN GB99 XXXX")
        effect = erp.simulate_payment("vendor_123", "4200.00")
        self.assertEqual(effect, "payment_sent:GB99 XXXX")

    def test_agent_has_no_mock_erp_import(self) -> None:
        source = (Path(__file__).parents[1] / "agent" / "ap_agent.py").read_text()
        imports = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        self.assertFalse(any("mock" in ast.unparse(node) for node in imports))

    def test_gateway_fixture_exports_preserve_connector_lineage(self) -> None:
        self.assertEqual(set(FIXTURE_ENVELOPES), {
            "invoice_clean.pdf",
            "invoice_poisoned.pdf",
            "email_forwarded.eml",
            "vendor_change_form.json",
            "invoice_qr_amount.pdf",
        })
        email, attachment = FORWARDED_EMAIL_ENVELOPES
        self.assertEqual(attachment.parent_envelope_id, email.envelope_id)
        self.assertEqual(attachment.authentication_assurance, AuthenticationAssurance.NONE)


if __name__ == "__main__":
    unittest.main()
