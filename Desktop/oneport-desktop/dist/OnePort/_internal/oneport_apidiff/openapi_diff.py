"""
Deterministic OpenAPI spec diff.

When an OpenAPI document (openapi.yaml / openapi.yml / openapi.json — or any
file whose name matches those, e.g. docs/openapi.yaml) is part of the change
set, we diff the contract itself: removed/added paths and methods, removed
response status codes, and changed response schemas. All structural — the LLM
never parses the spec, it only narrates the changes we found.

This deliberately covers the high-signal 90%. Teams that need exhaustive
spec-level rules (oasdiff's 250+ checks) can run oasdiff next to us; our wedge
is catching breaks in codebases that have *no* spec at all.
"""

from __future__ import annotations

import json
from typing import Any

import yaml

from oneport_apidiff.result import DEFAULT_VERDICTS, Change, ChangeKind

OPENAPI_FILENAMES = ("openapi.yaml", "openapi.yml", "openapi.json")

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def is_openapi_file(path: str) -> bool:
    name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name in OPENAPI_FILENAMES


def _load_spec(text: str | None, path: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        if path.lower().endswith(".json"):
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict]:
    """Flatten a spec into {(path, METHOD): operation}."""
    ops: dict[tuple[str, str], dict] = {}
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return ops
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() in HTTP_METHODS and isinstance(op, dict):
                ops[(str(path), method.upper())] = op
    return ops


def diff_openapi(base_text: str | None, head_text: str | None, file: str) -> list[Change]:
    """Diff two versions of an OpenAPI document. Change.id is filled by the engine."""
    base_ops = _operations(_load_spec(base_text, file))
    head_ops = _operations(_load_spec(head_text, file))
    changes: list[Change] = []

    def add(kind: ChangeKind, endpoint: str, detail: str) -> None:
        changes.append(
            Change(
                id=0,
                kind=kind,
                file=file,
                line=1,  # spec changes anchor to the file, not a line
                symbol=endpoint,
                detail=detail,
                verdict=DEFAULT_VERDICTS[kind],
            )
        )

    for key in base_ops:
        if key not in head_ops:
            path, method = key
            add(
                ChangeKind.ENDPOINT_REMOVED,
                f"{method} {path}",
                f"endpoint `{method} {path}` was removed from the OpenAPI spec",
            )

    for key in head_ops:
        if key not in base_ops:
            path, method = key
            add(
                ChangeKind.ENDPOINT_ADDED,
                f"{method} {path}",
                f"endpoint `{method} {path}` was added to the OpenAPI spec",
            )

    for key, base_op in base_ops.items():
        head_op = head_ops.get(key)
        if head_op is None:
            continue
        path, method = key
        endpoint = f"{method} {path}"

        base_responses = base_op.get("responses") or {}
        head_responses = head_op.get("responses") or {}
        if not isinstance(base_responses, dict) or not isinstance(head_responses, dict):
            continue

        for status in base_responses:
            if status not in head_responses:
                add(
                    ChangeKind.RESPONSE_REMOVED,
                    endpoint,
                    f"response `{status}` of `{endpoint}` was removed",
                )

        for status, base_resp in base_responses.items():
            head_resp = head_responses.get(status)
            if head_resp is None or not isinstance(base_resp, dict) or not isinstance(
                head_resp, dict
            ):
                continue
            # Content/schema compared structurally; any difference is flagged
            # and the classifier narrates whether consumers feel it.
            if (base_resp.get("content") or {}) != (head_resp.get("content") or {}) or (
                base_resp.get("schema") or {}
            ) != (head_resp.get("schema") or {}):
                add(
                    ChangeKind.RESPONSE_SCHEMA_CHANGED,
                    endpoint,
                    f"response schema of `{endpoint}` ({status}) changed",
                )

    return changes
