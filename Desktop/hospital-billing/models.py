"""models.py — the core data structures for the billing domain.

Plain dataclasses: a Patient, a LineItem on an invoice, and the Invoice itself.
No business logic beyond simple derived values — the money math lives in
billing.py so it can be tested in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Patient:
    """A patient and their insurance situation.

    coverage is the fraction (0.0 - 1.0) of the subtotal the insurer pays.
    """
    id: str
    name: str
    insurance_provider: str = "SELF-PAY"
    coverage: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.coverage <= 1.0:
            raise ValueError("coverage must be between 0.0 and 1.0")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LineItem:
    """One charged service on an invoice."""
    code: str
    description: str
    quantity: int
    unit_price: float

    @property
    def amount(self) -> float:
        """Total for this line: quantity x unit price."""
        return round(self.quantity * self.unit_price, 2)

    def to_dict(self) -> dict:
        return {**asdict(self), "amount": self.amount}


@dataclass
class Invoice:
    """A finished invoice: the patient, the line items, and the computed totals."""
    id: str
    patient: Patient
    items: list[LineItem] = field(default_factory=list)
    subtotal: float = 0.0
    insurance_paid: float = 0.0
    discount: float = 0.0
    tax: float = 0.0
    total: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "patient": self.patient.to_dict(),
            "items": [i.to_dict() for i in self.items],
            "subtotal": self.subtotal,
            "insurance_paid": self.insurance_paid,
            "discount": self.discount,
            "tax": self.tax,
            "total": self.total,
        }
