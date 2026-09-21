"""api.py — a small Flask REST API over the billing engine.

Endpoints:
  GET  /services            list the service catalog
  POST /patients            register a patient        -> {id, ...}
  POST /invoices            create an invoice for a patient + services
  GET  /invoices/<id>       fetch a stored invoice

Run:  python api.py   (serves on http://127.0.0.1:5000)
"""
from __future__ import annotations

import uuid

from flask import Flask, jsonify, request

import database
import pricing
from billing import BillingEngine
from models import Patient

app = Flask(__name__)
_engine = BillingEngine()


@app.get("/services")
def services():
    return jsonify(pricing.list_services())


@app.post("/patients")
def create_patient():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    patient = Patient(
        id=uuid.uuid4().hex[:8],
        name=name,
        insurance_provider=body.get("insurance_provider", "SELF-PAY"),
        coverage=float(body.get("coverage", 0.0)),
    )
    database.save_patient(patient.to_dict())
    return jsonify(patient.to_dict()), 201


@app.post("/invoices")
def create_invoice():
    body = request.get_json(silent=True) or {}
    patient_id = body.get("patient_id")
    stored = database.get_patient(patient_id) if patient_id else None
    if not stored:
        return jsonify({"error": "unknown patient_id"}), 404

    services_in = body.get("services") or []
    try:
        services_list = [(s["code"], int(s.get("quantity", 1))) for s in services_in]
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "services must be [{code, quantity}]"}), 400

    try:
        invoice = _engine.create_invoice(
            Patient(**stored),
            services_list,
            discount=float(body.get("discount", 0.0)),
        )
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 400

    database.save_invoice(invoice.to_dict())
    return jsonify(invoice.to_dict()), 201


@app.get("/invoices/<invoice_id>")
def get_invoice(invoice_id: str):
    invoice = database.get_invoice(invoice_id)
    if not invoice:
        return jsonify({"error": "not found"}), 404
    return jsonify(invoice)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
