"""test_billing.py — unit tests for the billing math."""
from __future__ import annotations

import pytest

import billing
from billing import BillingEngine, build_line_item, subtotal, tax
from models import Patient


def test_line_item_amount():
    item = build_line_item("LAB-CBC", 2)
    assert item.amount == pytest.approx(90.00)   # 45.00 x 2


def test_subtotal_sums_items():
    items = [build_line_item("CONS-GEN", 1), build_line_item("LAB-CBC", 1)]
    assert subtotal(items) == pytest.approx(165.00)   # 120 + 45


def test_tax_rate():
    assert tax(100.0) == pytest.approx(5.00)          # 5%


def test_build_line_item_rejects_zero_quantity():
    with pytest.raises(ValueError):
        build_line_item("CONS-GEN", 0)


def test_unknown_service_code():
    with pytest.raises(KeyError):
        build_line_item("NOPE-000", 1)


def test_patient_rejects_bad_coverage():
    with pytest.raises(ValueError):
        Patient(id="x", name="Bad", coverage=1.5)


def test_full_invoice_with_insurance_and_discount():
    patient = Patient(id="P-1", name="Test", insurance_provider="Acme", coverage=0.8)
    engine = BillingEngine()
    invoice = engine.create_invoice(
        patient,
        services=[("CONS-GEN", 1), ("LAB-CBC", 1)],   # subtotal 165
        discount=10.0,
    )
    assert invoice.subtotal == pytest.approx(165.00)
    assert invoice.insurance_paid == pytest.approx(132.00)   # 80% of 165
    responsible = 165.00 - 132.00                            # 33.00
    after_discount = responsible - 10.0                      # 23.00
    expected_tax = round(after_discount * billing.TAX_RATE, 2)
    assert invoice.tax == pytest.approx(expected_tax)
    assert invoice.total == pytest.approx(after_discount + expected_tax)


def test_self_pay_has_no_insurance():
    patient = Patient(id="P-2", name="SelfPay")
    engine = BillingEngine()
    invoice = engine.create_invoice(patient, services=[("CONS-GEN", 1)])
    assert invoice.insurance_paid == pytest.approx(0.0)
