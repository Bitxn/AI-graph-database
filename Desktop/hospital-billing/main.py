"""main.py — CLI demo entrypoint.

Builds a sample patient, charges a few services, and prints a formatted,
itemized invoice. Run with:  python main.py
"""
from __future__ import annotations

from billing import BillingEngine
from models import Patient


def format_invoice(invoice) -> str:
    """Render an Invoice as a plain-text itemized bill."""
    lines = []
    lines.append("=" * 48)
    lines.append(f"INVOICE {invoice.id}")
    lines.append(f"Patient: {invoice.patient.name}")
    lines.append(f"Insurer: {invoice.patient.insurance_provider} "
                 f"({int(invoice.patient.coverage * 100)}% coverage)")
    lines.append("-" * 48)
    for item in invoice.items:
        lines.append(f"{item.description:<30} {item.quantity:>2} x "
                     f"{item.unit_price:>8.2f} = {item.amount:>9.2f}")
    lines.append("-" * 48)
    lines.append(f"{'Subtotal':<38} {invoice.subtotal:>9.2f}")
    lines.append(f"{'Insurance paid':<38} {-invoice.insurance_paid:>9.2f}")
    lines.append(f"{'Discount':<38} {-invoice.discount:>9.2f}")
    lines.append(f"{'Tax':<38} {invoice.tax:>9.2f}")
    lines.append("=" * 48)
    lines.append(f"{'TOTAL DUE':<38} {invoice.total:>9.2f}")
    lines.append("=" * 48)
    return "\n".join(lines)


def main() -> None:
    patient = Patient(
        id="P-001",
        name="Jane Doe",
        insurance_provider="BlueCross",
        coverage=0.8,
    )
    engine = BillingEngine()
    invoice = engine.create_invoice(
        patient,
        services=[
            ("CONS-SPEC", 1),
            ("LAB-CBC", 1),
            ("IMG-MRI", 1),
            ("ROOM-GEN", 2),
            ("MED-DISP", 3),
        ],
        discount=50.0,
    )
    print(format_invoice(invoice))


if __name__ == "__main__":
    main()
