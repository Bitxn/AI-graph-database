# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Doctype registry — maps a `--type` key to (canonical title, builder).

Every builder shares one signature so the CLI can dispatch uniformly:

    async def builder(index, meta, llm=None, scope_module=None) -> Document

This is the seam the phased roadmap plugs into: Phase 1 shipped the narrative
Technical Design Document; Phase 2 adds the deterministic SBOM; future doctypes
(threat model, ADR log) register here without touching the CLI or renderers.
"""
from __future__ import annotations

from oneport_docgen.content import build_document as _build_tdd
from oneport_docgen.sbom import build_sbom as _build_sbom

# key -> (canonical document title, async builder)
DOCTYPES: dict[str, tuple[str, object]] = {
    "tdd":        ("Technical Design Document", _build_tdd),
    "technical":  ("Technical Design Document", _build_tdd),
    "sdd":        ("Software Design Description", _build_tdd),
    "design":     ("Software Design Description", _build_tdd),
    "onboarding": ("Engineering Onboarding Guide", _build_tdd),
    "sbom":       ("Software Bill of Materials", _build_sbom),
}

DEFAULT = "tdd"


def resolve(key: str) -> tuple[str, object]:
    """Return (title, builder) for a doctype key, defaulting to the TDD."""
    return DOCTYPES.get((key or "").lower().strip(), DOCTYPES[DEFAULT])


def is_known(key: str) -> bool:
    return (key or "").lower().strip() in DOCTYPES


def keys() -> list[str]:
    return list(DOCTYPES)
