"""
Terraform parsing.

Two sources, both fully static:

  parse_tf_file(path)  — a `.tf` HCL file. A lightweight block scanner pulls out
                         `resource "TYPE" "NAME" { ... }` blocks and their scalar
                         attributes (including one level of nested blocks, e.g.
                         `root_block_device { volume_size = 100 }`). This is not a
                         full HCL engine — variables/interpolations are kept as
                         their literal text — but it reliably recovers the sizing
                         attributes cost analysis needs.

  parse_tf_plan(path)  — `terraform show -json` output the user provides. This is
                         the accurate source: variables are resolved. We read
                         planned_values.root_module (recursively through child
                         modules) and normalise the same way.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from costwatch.exceptions import ParseError
from costwatch.pricing import instance_monthly, storage_monthly
from costwatch.result import Resource

# `resource "aws_instance" "web" {`  — captures type, name, and the body start.
_RESOURCE_RE = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{')

# A scalar assignment on its own line:  key = "value" | 123 | true | var.foo
_ASSIGN_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*(?:#.*)?$')

# A nested block opener:  root_block_device {
_BLOCK_OPEN_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:"[^"]*"\s*)?\{\s*$')


def parse_tf_file(path: str | Path) -> list[Resource]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ParseError(f"cannot read {path}: {exc}") from exc
    return parse_tf_text(text, str(path.as_posix()))


def parse_tf_text(text: str, filename: str = "") -> list[Resource]:
    """Parse `.tf` HCL source from a string (used for in-memory PR file content)."""
    resources: list[Resource] = []
    for match in _RESOURCE_RE.finditer(text):
        kind, name = match.group(1), match.group(2)
        body, _ = _capture_block(text, match.end())
        line = text.count("\n", 0, match.start()) + 1
        attrs = _parse_body(body)
        resources.append(_normalise(kind, name, attrs, filename, line))
    return resources


def parse_tf_plan(path: str | Path) -> list[Resource]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ParseError(f"cannot parse plan JSON {path}: {exc}") from exc

    root = (data.get("planned_values") or {}).get("root_module") or {}
    resources: list[Resource] = []
    _walk_plan_module(root, str(path.as_posix()), resources)
    return resources


def _walk_plan_module(module: dict[str, Any], src: str, out: list[Resource]) -> None:
    for res in module.get("resources", []) or []:
        if res.get("mode") != "managed":
            continue
        kind = res.get("type", "")
        name = res.get("name", "")
        values = res.get("values", {}) or {}
        out.append(_normalise(kind, name, _flatten_plan_values(values), src, 0))
    for child in module.get("child_modules", []) or []:
        _walk_plan_module(child, src, out)


def _flatten_plan_values(values: dict[str, Any]) -> dict[str, str]:
    """Flatten a plan resource's `values` into the same dotted-key shape the
    `.tf` scanner produces, so normalisation is source-agnostic."""
    flat: dict[str, str] = {}
    for key, val in values.items():
        if isinstance(val, (str, int, float, bool)):
            flat[key] = str(val)
        elif isinstance(val, list) and val and isinstance(val[0], dict):
            # e.g. root_block_device = [{"volume_size": 100, ...}]
            for subkey, subval in val[0].items():
                if isinstance(subval, (str, int, float, bool)):
                    flat[f"{key}.{subkey}"] = str(subval)
    return flat


# ── HCL block scanning ─────────────────────────────────────────────────────────

def _capture_block(text: str, start: int) -> tuple[str, int]:
    """Return (body, end_index) for the block whose opening `{` was just consumed
    at `start`, using brace matching. Braces inside strings are ignored."""
    depth = 1
    i = start
    in_string = False
    quote = ""
    while i < len(text) and depth > 0:
        ch = text[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                in_string = False
        elif ch in '"\'':
            in_string = True
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i], i + 1
        i += 1
    return text[start:i], i


def _parse_body(body: str) -> dict[str, str]:
    """Extract scalar attributes from a block body, including one level of nested
    blocks (recorded as `blockname.key`). The `count` meta-argument is captured
    too. Values keep their literal text (quotes stripped)."""
    attrs: dict[str, str] = {}
    lines = body.splitlines()
    i = 0
    prefix = ""
    depth_stack: list[str] = []
    while i < len(lines):
        line = lines[i]
        block_open = _BLOCK_OPEN_RE.match(line)
        assign = _ASSIGN_RE.match(line)
        if assign and "{" not in assign.group(2):
            key = (f"{prefix}.{assign.group(1)}" if prefix else assign.group(1))
            attrs[key] = _clean_value(assign.group(2))
        elif block_open and not assign:
            depth_stack.append(prefix)
            prefix = block_open.group(1)
        elif line.strip() == "}" and depth_stack:
            prefix = depth_stack.pop()
        i += 1
    return attrs


def _clean_value(raw: str) -> str:
    raw = raw.strip().rstrip(",")
    if len(raw) >= 2 and raw[0] in '"\'' and raw[-1] == raw[0]:
        return raw[1:-1]
    return raw


# ── Normalisation ──────────────────────────────────────────────────────────────

def _int(val: str | None, default: int = 1) -> int:
    try:
        return int(float(val)) if val is not None else default
    except (ValueError, TypeError):
        return default


def _normalise(kind: str, name: str, attrs: dict[str, str], file: str, line: int) -> Resource:
    """Map a raw parsed block onto a priced Resource by resource type."""
    provider = "gcp" if kind.startswith("google_") else "aws"
    count = _int(attrs.get("count"), 1)

    size = ""
    monthly: float | None = None

    if kind == "aws_instance":
        size = attrs.get("instance_type", "")
        monthly = instance_monthly(size)
    elif kind == "aws_db_instance":
        size = attrs.get("instance_class", "")
        monthly = instance_monthly(size)
    elif kind in ("aws_elasticache_cluster", "aws_elasticache_replication_group"):
        size = attrs.get("node_type", "")
        monthly = instance_monthly(size)
        count = _int(
            attrs.get("num_cache_nodes") or attrs.get("num_cache_clusters"), count
        )
    elif kind == "aws_ebs_volume":
        size = attrs.get("type", "gp3")
        monthly = storage_monthly(float(_int(attrs.get("size"), 0)), size)
    elif kind == "aws_autoscaling_group":
        size = ""  # instance type lives in the launch template — not linked statically
        count = _int(attrs.get("desired_capacity"), count)
    elif kind == "google_compute_instance":
        size = attrs.get("machine_type", "")
        monthly = instance_monthly(size)
    elif kind == "google_compute_disk":
        size = attrs.get("type", "pd-standard")
        monthly = storage_monthly(float(_int(attrs.get("size"), 0)), size)
    elif kind == "aws_ecs_service":
        count = _int(attrs.get("desired_count"), count)

    return Resource(
        kind=kind,
        name=name,
        provider=provider,
        file=file,
        line=line,
        size=size,
        count=max(count, 1),
        attributes=attrs,
        monthly_cost=monthly,
    )
