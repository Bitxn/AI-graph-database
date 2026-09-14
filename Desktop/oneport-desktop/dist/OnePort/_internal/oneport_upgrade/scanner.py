"""
The scanner — deterministic AST detection of a migration's rules.

Walks every `.py` file once. Precision matters: a rule scoped to a module (Django's
`url` from `django.conf.urls`, `force_text` from `django.utils.encoding`) fires ONLY
when the symbol was actually imported from there — so a local `url()` helper is never
mistaken for the removed Django one. Attribute rules are gated on their syntactic
owner (`timezone.utc`). No model, no execution; every hit carries file:line:col.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

from oneport_upgrade.result import Finding
from oneport_upgrade.rules.schema import Migration, Rule

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
              ".pytest_cache", "dist", "build", ".tox", ".idea", "site-packages", "migrations"}
_MAX_FILE_BYTES = 1_500_000


class _RuleSet:
    def __init__(self, rules: list[Rule]) -> None:
        self.imports: dict[str, Rule] = {}
        self.from_imports: dict[tuple[str, str], Rule] = {}
        self.calls: dict[str, Rule] = {}
        self.attrs: dict[str, Rule] = {}
        self.decorators: dict[str, Rule] = {}
        self.bases: dict[str, Rule] = {}
        self.names: dict[str, Rule] = {}
        for r in rules:
            if r.kind == "from_import":
                self.from_imports[(r.module, r.match)] = r
            else:
                getattr(self, r.kind + ("s" if not r.kind.endswith("s") else "")).__setitem__(r.match, r)


def scan_repo(root: str | Path, migration: Migration) -> tuple[list[Finding], int]:
    root = Path(root).resolve()
    ruleset = _RuleSet(migration.rules)
    findings: list[Finding] = []
    scanned = 0

    files, base = ([root], root.parent) if root.is_file() else (list(_iter_py(root)), root)
    for fpath in files:
        try:
            raw = fpath.read_bytes()
        except OSError:
            continue
        if len(raw) > _MAX_FILE_BYTES:
            continue
        try:
            source = raw.decode("utf-8")
            tree = ast.parse(source)
        except (UnicodeDecodeError, SyntaxError):
            continue
        scanned += 1
        rel = str(fpath.relative_to(base)).replace(os.sep, "/")
        findings.extend(_scan_tree(tree, rel, source.splitlines(), ruleset))
    return findings, scanned


def _iter_py(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _import_map(tree: ast.Module) -> dict[str, str]:
    """Map a locally-visible name → the module it was imported from."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                out[alias.asname or alias.name] = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out[alias.asname or alias.name.split(".")[0]] = alias.name
    return out


def _module_ok(name: str, rule: Rule, imports: dict[str, str]) -> bool:
    """For call/name rules scoped to a module, require the name came from it."""
    if not rule.module:
        return True
    src = imports.get(name, "")
    return bool(src) and (src == rule.module or src.startswith(rule.module + "."))


def _owner_ok(node: ast.Attribute, rule: Rule, imports: dict[str, str]) -> bool:
    """For attr rules scoped to an owner (timezone.utc), check the receiver name —
    and, when the rule pins `owner_from`, that the receiver was imported from that
    module. This stops stdlib `from datetime import timezone; timezone.utc` from
    being mistaken for the deprecated `django.utils.timezone.utc`.
    """
    if not rule.module:
        return True
    val = node.value
    receiver_ok = (isinstance(val, ast.Name) and val.id == rule.module) or \
                  (isinstance(val, ast.Attribute) and val.attr == rule.module)
    if not receiver_ok:
        return False
    if rule.owner_from and isinstance(val, ast.Name):
        src = imports.get(val.id, "")
        return bool(src) and (src == rule.owner_from or src.startswith(rule.owner_from + "."))
    return True


def _callee(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


# Receiver names that are overwhelmingly an HTTP response/client, not a model:
# response.json(), resp.dict(), api_response.json(). In FastAPI's own suite,
# 1,668 of ~1,700 `.json()` calls have one of these receivers. Numbered variants
# (response2) included; `r` is the requests-library idiom.
_HTTP_RECV_RE = re.compile(
    r"^(responses?|resp|res|reply|requests?|req|client|api_response|followed|r)\d*$",
    re.IGNORECASE,
)


def _http_response_receiver(func: ast.expr) -> bool:
    """True when a method call's receiver looks like an HTTP response/client."""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return bool(_HTTP_RECV_RE.match(func.value.id))
    return False


def _file_imports_prefix(imports: dict[str, str], prefix: str) -> bool:
    """True if any import in this file comes from `prefix` (module or submodule).

    `imports` maps local-name -> source module, so an `import pydantic` or
    `from pydantic import BaseModel` both leave a source starting with 'pydantic'.
    """
    return any(src == prefix or src.startswith(prefix + ".") or src == prefix
               for src in imports.values())


def _scan_tree(tree: ast.Module, rel: str, lines: list[str], rs: _RuleSet) -> list[Finding]:
    imports = _import_map(tree)
    found: list[Finding] = []

    def emit(rule: Rule, node: ast.AST) -> None:
        # Generic method rules (.json/.dict/.copy) only make sense where the
        # library is actually in use — otherwise it's httpx/requests/stdlib.
        if rule.requires_import and not _file_imports_prefix(imports, rule.requires_import):
            return
        lineno = getattr(node, "lineno", 0)
        snippet = lines[lineno - 1].strip()[:200] if 0 < lineno <= len(lines) else ""
        found.append(Finding(
            rule_id=rule.id, title=rule.title, severity=rule.severity, file=rel,
            line=lineno, col=getattr(node, "col_offset", 0), snippet=snippet,
            replacement=rule.replacement, auto=rule.auto, hint=rule.hint))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if r := (rs.imports.get(alias.name) or rs.imports.get(alias.name.split(".")[0])):
                    emit(r, node)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                if r := rs.from_imports.get((mod, alias.name)):
                    emit(r, node)
        elif isinstance(node, ast.Call):
            name = _callee(node.func)
            if name and (r := rs.calls.get(name)) and _module_ok(name, r, imports):
                # Generic method rules (requires_import set): skip obvious HTTP
                # receivers even in pydantic-importing files — a test module that
                # defines a model AND calls response.json() is the common case.
                if r.requires_import and _http_response_receiver(node.func):
                    continue
                emit(r, node)
        elif isinstance(node, ast.Attribute):
            if (r := rs.attrs.get(node.attr)) and _owner_ok(node, r, imports):
                emit(r, node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in getattr(node, "decorator_list", []):
                dname = _callee(dec.func) if isinstance(dec, ast.Call) else _callee(dec)
                if dname and (r := rs.decorators.get(dname)):
                    emit(r, dec)
            for base in getattr(node, "bases", []):
                if (bname := _callee(base)) and (r := rs.bases.get(bname)):
                    emit(r, base)
        elif isinstance(node, ast.Name):
            if (r := rs.names.get(node.id)) and _module_ok(node.id, r, imports):
                emit(r, node)

    return found
