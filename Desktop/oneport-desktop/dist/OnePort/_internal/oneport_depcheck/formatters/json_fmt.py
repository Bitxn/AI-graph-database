"""JSON formatter — the full ScanResult, machine-readable, for CI pipelines."""

from __future__ import annotations

import json

from oneport_depcheck.result import ScanResult


def format_json(result: ScanResult) -> str:
    return json.dumps(result.to_dict(), indent=2, default=str)
