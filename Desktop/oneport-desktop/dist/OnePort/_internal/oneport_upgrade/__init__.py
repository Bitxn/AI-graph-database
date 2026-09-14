"""
oneport-upgrade — the framework/version upgrade copilot.

Detect deprecated and removed APIs (Python, Django, Pydantic) deterministically,
apply the safe codemods, and verify with your own test suite. Runs on your machine.

Part of the Oneport developer OS. Detection and transforms are deterministic; the
model only writes the migration plan.
"""

__version__ = "0.2.0"
