"""
The codemod engine — applies the SAFE, mechanical fixes to the working tree.

Only `auto` findings are touched, and only in these forms:
  * call/attr/name rename   token → replacement, word-boundary, on the exact line;
  * from_import move        `from OLD import NAME` → `from NEW import NAME`, and only
                            when that line imports the single moved name (a mixed
                            import like `from collections import Mapping, OrderedDict`
                            is left for a human — moving it would break OrderedDict).

Safety net: after editing a file we re-parse it. If the result doesn't parse, the
whole file is reverted and those findings are reported as not applied. We never
write syntactically broken code.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from oneport_upgrade.result import Finding
from oneport_upgrade.rules.schema import Migration, Rule


def apply_fixes(
    root: str | Path, findings: list[Finding], migration: Migration, dry_run: bool = False,
) -> list[str]:
    """
    Apply every auto-fixable finding in place (or preview when dry_run). Mutates
    each applied Finding's `.applied = True`. Returns a list of unified-diff-ish
    preview strings (populated in dry_run; empty otherwise).
    """
    root = Path(root)
    rule_by_id = {r.id: r for r in migration.rules}
    by_file: dict[str, list[Finding]] = {}
    for f in findings:
        if f.auto and not f.applied:
            by_file.setdefault(f.file, []).append(f)

    previews: list[str] = []
    for rel, file_findings in by_file.items():
        path = root / rel if not root.is_file() else root
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)

        applied_here: list[Finding] = []
        for f in file_findings:
            rule = rule_by_id.get(f.rule_id)
            if rule is None or not (0 < f.line <= len(lines)):
                continue
            new_line = _rewrite_line(lines[f.line - 1], rule)
            if new_line is not None and new_line != lines[f.line - 1]:
                lines[f.line - 1] = new_line
                applied_here.append(f)

        if not applied_here:
            continue
        new_source = "".join(lines)
        try:
            ast.parse(new_source)
        except SyntaxError:
            continue  # revert: don't write, don't mark applied

        for f in applied_here:
            f.applied = True
        if dry_run:
            previews.append(f"--- {rel}\n+++ {rel} (after {len(applied_here)} fix(es))")
        else:
            path.write_text(new_source, encoding="utf-8")

    return previews


def _rewrite_line(line: str, rule: Rule) -> str | None:
    if rule.kind == "from_import":
        return _rewrite_import(line, rule)
    if rule.replacement and rule.kind in ("call", "attr", "name"):
        return re.sub(rf"\b{re.escape(rule.match)}\b", rule.replacement, line)
    return None


def _rewrite_import(line: str, rule: Rule) -> str | None:
    """
    Rewrite a single-name `from` import for two cases (an `as alias` is preserved):
      * module move   `from collections import Mapping` → `from collections.abc import Mapping`
      * name rename   `from ...translation import ugettext as _` → `... import gettext as _`
    A multi-name line (`from collections import Mapping, OrderedDict`) is left for a
    human — moving/renaming it could break the other names.
    """
    body = line.rstrip("\n")
    m = re.match(
        rf"^(\s*from\s+){re.escape(rule.module)}\s+import\s+{re.escape(rule.match)}"
        rf"(\s+as\s+\w+)?(\s*(?:#.*)?)$", body)
    if not m:
        return None
    new_module = rule.new_module or rule.module
    new_name = rule.replacement or rule.match
    alias, trailing = m.group(2) or "", m.group(3) or ""
    newline = "\n" if line.endswith("\n") else ""
    return f"{m.group(1)}{new_module} import {new_name}{alias}{trailing}{newline}"
