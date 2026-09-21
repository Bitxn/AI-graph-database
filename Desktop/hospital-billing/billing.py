"""billing.py — the billing engine (all the money math lives here).

Given a patient and the services they received, it builds an Invoice: subtotal,
insurance adjustment, optional discount, tax, and the final patient total. Kept
free of I/O so every step is unit-testable.
"""
from __future__ import annotations

import uuid

import pricing
from models import Patient, LineItem, Invoice

# Sales/health-service tax applied to the patient-responsible amount.
TAX_RATE = 0.05


def build_line_item(code: str, quantity: int = 1) -> LineItem:
    """Create a LineItem for a service code, pulling price from the catalog."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    return LineItem(
        code=code,
        description=pricing.get_description(code),
        quantity=quantity,
        unit_price=pricing.get_price(code),
    )


def subtotal(items: list[LineItem]) -> float:
    """Sum of every line item's amount."""
    return round(sum(item.amount for item in items), 2)


def insurance_adjustment(amount: float, coverage: float) -> float:
    """How much the insurer pays: coverage fraction of the amount."""
    return round(amount * coverage, 2)


def apply_discount(amount: float, discount: float) -> float:
    """Subtract a flat discount, never dropping below zero."""
    return round(max(amount - discount, 0.0), 2)


def tax(amount: float) -> float:
    """Tax on a (patient-responsible) amount."""
    return round(amount * TAX_RATE, 2)


class BillingEngine:
    """Assembles invoices from a patient plus a list of (code, quantity)."""

    def create_invoice(
        self,
        patient: Patient,
        services: list[tuple[str, int]],
        discount: float = 0.0,
    ) -> Invoice:
        items = [build_line_item(code, qty) for code, qty in services]
        sub = subtotal(items)
        insured = insurance_adjustment(sub, patient.coverage)
        responsible = round(sub - insured, 2)
        after_discount = apply_discount(responsible, discount)
        applied_tax = tax(after_discount)
        total = round(after_discount + applied_tax, 2)

        return Invoice(
            id=uuid.uuid4().hex[:12],
            patient=patient,
            items=items,
            subtotal=sub,
            insurance_paid=insured,
            discount=round(min(discount, responsible), 2),
            tax=applied_tax,
            total=total,
        )
