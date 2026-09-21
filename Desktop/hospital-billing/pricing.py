"""pricing.py — the hospital service catalog.

Maps a short service code to a human description and a unit price (USD). This is
the single source of truth for what things cost; billing.py reads prices here.
"""
from __future__ import annotations

# code -> (description, unit_price_usd)
SERVICES: dict[str, tuple[str, float]] = {
    "CONS-GEN": ("General consultation", 120.00),
    "CONS-SPEC": ("Specialist consultation", 250.00),
    "LAB-CBC": ("Complete blood count", 45.00),
    "LAB-LIPID": ("Lipid panel", 65.00),
    "IMG-XRAY": ("X-ray, single region", 180.00),
    "IMG-MRI": ("MRI scan", 1200.00),
    "ROOM-GEN": ("General ward, per night", 400.00),
    "ROOM-ICU": ("ICU, per night", 2500.00),
    "PROC-SUTURE": ("Wound suturing", 300.00),
    "MED-DISP": ("Medication dispensing fee", 15.00),
}


def get_price(code: str) -> float:
    """Unit price for a service code. Raises KeyError for an unknown code."""
    try:
        return SERVICES[code][1]
    except KeyError as exc:
        raise KeyError(f"Unknown service code: {code}") from exc


def get_description(code: str) -> str:
    """Human-readable description for a service code."""
    return SERVICES[code][0]


def list_services() -> list[dict]:
    """The full catalog as a list of {code, description, unit_price}."""
    return [
        {"code": code, "description": desc, "unit_price": price}
        for code, (desc, price) in sorted(SERVICES.items())
    ]
