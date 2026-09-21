"""database.py — tiny JSON-file persistence for patients and invoices.

Not a real database — a single JSON file under ./data — but enough to make the
API stateful across requests and to exercise a storage layer. Thread-safety and
concurrency are out of scope for this demo.
"""
from __future__ import annotations

import json
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"
_STORE = _DATA_DIR / "store.json"


def _empty() -> dict:
    return {"patients": {}, "invoices": {}}


def _load() -> dict:
    if not _STORE.exists():
        return _empty()
    try:
        return json.loads(_STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty()


def _save(data: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def save_patient(patient_dict: dict) -> None:
    data = _load()
    data["patients"][patient_dict["id"]] = patient_dict
    _save(data)


def get_patient(patient_id: str) -> dict | None:
    return _load()["patients"].get(patient_id)


def save_invoice(invoice_dict: dict) -> None:
    data = _load()
    data["invoices"][invoice_dict["id"]] = invoice_dict
    _save(data)


def get_invoice(invoice_id: str) -> dict | None:
    return _load()["invoices"].get(invoice_id)


def list_invoices() -> list[dict]:
    return list(_load()["invoices"].values())
