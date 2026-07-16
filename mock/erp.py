"""A tiny ERP with an intentionally dangerous free-text routing alias."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal


_ROUTE_HINT = re.compile(r"route(?: payments)? to IBAN\s+([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){4,30})", re.I)


@dataclass
class Vendor:
    vendor_id: str
    name: str
    bank_account: str
    remittance_note: str = ""


@dataclass
class MockERP:
    vendors: dict[str, Vendor] = field(default_factory=dict)
    ap_ledger: list[dict[str, str]] = field(default_factory=list)
    payments: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.vendors:
            self.vendors["vendor_123"] = Vendor(
                vendor_id="vendor_123",
                name="Northwind Components",
                bank_account="DE89370400440532013000",
            )

    def post_invoice(self, vendor_id: str, invoice_id: str, amount: str) -> str:
        parsed = Decimal(amount.replace(",", "").replace("$", ""))
        self.ap_ledger.append(
            {"vendor_id": vendor_id, "invoice_id": invoice_id, "amount": f"{parsed:.2f}"}
        )
        return f"invoice posted, ${parsed:,.0f}"

    def update_bank_account(self, vendor_id: str, bank_account: str) -> str:
        self.vendors[vendor_id].bank_account = bank_account
        return "bank_account_updated"

    def update_remittance_note(self, vendor_id: str, note: str) -> str:
        self.vendors[vendor_id].remittance_note = note
        return "remittance_note_updated"

    def simulate_payment(self, vendor_id: str, amount: str) -> str:
        """Route money, including via the genuine free-text aliasing hazard."""

        vendor = self.vendors[vendor_id]
        routing_account = vendor.bank_account
        hint = _ROUTE_HINT.search(vendor.remittance_note)
        if hint:
            routing_account = hint.group(1)
        self.payments.append(
            {"vendor_id": vendor_id, "amount": amount, "routed_to": routing_account}
        )
        return f"payment_sent:{routing_account}"

    def apply(self, destination_system: str, canonical_field: str, value: str) -> str:
        """Destination-side dispatcher intended to be called only by a broker."""

        if destination_system == "ap_ledger" and canonical_field == "ap.invoice.amount":
            return self.post_invoice("vendor_123", "NW-1042", value)
        if destination_system == "vendor_master" and canonical_field == "vendor.remittance.bank_account":
            return self.update_bank_account("vendor_123", value)
        if destination_system == "vendor_master" and canonical_field == "vendor.remittance.note":
            return self.update_remittance_note("vendor_123", value)
        raise ValueError(f"unsupported ERP mutation: {destination_system}.{canonical_field}")
