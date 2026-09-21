# Hospital Billing System

A small, self-contained hospital billing engine. It turns a patient plus a list
of rendered services into an itemized invoice — applying the service catalog,
insurance coverage, discounts, and tax — and exposes it over a small REST API.

## Structure

| File | Role | What it does |
|------|------|--------------|
| `main.py` | entrypoint | CLI demo that builds and prints a sample invoice |
| `api.py` | api | Flask REST API (services, patients, invoices) |
| `billing.py` | core logic | Computes subtotal, insurance, discount, tax, total |
| `models.py` | model | `Patient`, `LineItem`, `Invoice` data classes |
| `pricing.py` | catalog | Service codes and unit prices |
| `database.py` | storage | JSON-file persistence for patients and invoices |
| `tests/test_billing.py` | test | Unit tests for the billing math |

## Quick start

```bash
pip install -r requirements.txt
python main.py              # print a sample itemized invoice
python api.py               # run the REST API on http://127.0.0.1:5000
pytest                      # run the tests
```

## Billing model

For each invoice:

1. **Subtotal** = sum of `quantity x unit_price` across line items.
2. **Insurance** covers `coverage x subtotal` (coverage is a fraction 0..1).
3. **Discount** (optional) is applied to the patient's remaining responsibility.
4. **Tax** is applied to the discounted, patient-responsible amount.
5. **Total** = patient responsibility - discount + tax.

All money is handled in whole cents internally where it matters and rounded to
two decimals on output.
