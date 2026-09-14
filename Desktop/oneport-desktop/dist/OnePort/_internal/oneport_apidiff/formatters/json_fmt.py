"""
JSON formatter — machine-readable, stable shape for CI pipelines and jq.
"""

from __future__ import annotations

import json

from oneport_apidiff.result import ApiDiffResult


def format_json(result: ApiDiffResult) -> str:
    return json.dumps(result.to_dict(), indent=2)
