"""
Internal-caller scan — text-based, deterministic, and *usage-shaped*.

For each changed symbol, walk the repo's .py files and report every line that
genuinely USES the symbol — a call `name(...)`, an attribute/method access
`obj.name`, or an import of it. Lines that merely contain the word (a parameter
declaration `def f(name)`, an assignment `name = ...`, a keyword argument
`f(name=...)`, a string, or a comment) are NOT callers.

This is the v0.2.0 precision fix: the old scan matched the bare word anywhere,
which in a large monorepo drowned real callers in noise — and noise is exactly
what makes a team switch a gate off. It stays a textual over-approximation (no
type inference), so the classifier prompt still says so; it is simply far
tighter about what counts as a use.
"""

from __future__ import annotations

import re
from pathlib import Path

from oneport_apidiff.result import CallerRef

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "build",
    "dist",
    ".eggs",
}

MAX_CALLERS_PER_SYMBOL = 20


def _matchers(name: str) -> tuple[re.Pattern, re.Pattern, re.Pattern, re.Pattern]:
    esc = re.escape(name)
    word = re.compile(rf"\b{esc}\b")
    # A call `name(` where `name` is not part of a longer identifier and not an
    # attribute access (obj.name(...) is handled by the attribute matcher).
    call = re.compile(rf"(?<![.\w]){esc}\s*\(")
    # An attribute access/call `.name` that is not an assignment `.name = `.
    attr = re.compile(rf"\.\s*{esc}\b(?!\s*=(?!=))")
    definition = re.compile(rf"(async\s+)?(def|class)\s+{esc}\b")
    return word, call, attr, definition


def _import_re() -> re.Pattern:
    return re.compile(r"^\s*(from\s+\S+\s+import\b|import\b)")


def _usage_line(line: str, name: str, mats) -> bool:
    """Does `line` use `name` as a call, attribute access, or import?"""
    word, call, attr, definition = mats
    if not word.search(line):
        return False
    stripped = line.strip()
    if stripped.startswith("#"):
        return False
    if definition.match(stripped):  # the def/class of this very name is not a use
        return False
    if _IMPORT.match(stripped):  # `from x import name` / `import name`
        return True
    return bool(call.search(line) or attr.search(line))


_IMPORT = _import_re()


def _scan_text(
    text: str,
    rel: str,
    name: str,
    mats,
    def_norm: str,
    definition_line: int,
    out: list[CallerRef],
) -> bool:
    """Append callers found in `text`; return True once the cap is hit."""
    for lineno, line in enumerate((text or "").splitlines(), start=1):
        if rel == def_norm and lineno == definition_line:
            continue
        if not _usage_line(line, name, mats):
            continue
        out.append(CallerRef(file=rel, line=lineno, snippet=line.strip()[:200]))
        if len(out) >= MAX_CALLERS_PER_SYMBOL:
            return True
    return False


def _iter_py_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def find_callers_in_texts(
    files: dict[str, str],
    symbol: str,
    defining_file: str,
    definition_line: int = 0,
) -> list[CallerRef]:
    """
    In-memory variant of find_callers for PR mode without a local checkout:
    scans only the fetched head texts of the PR's changed files. Narrower than
    a full repo scan — the sticky comment says so when this path is used.
    """
    name = symbol.rsplit(".", 1)[-1]
    mats = _matchers(name)
    def_norm = defining_file.replace("\\", "/")

    callers: list[CallerRef] = []
    for rel, text in files.items():
        if _scan_text(text, rel.replace("\\", "/"), name, mats, def_norm, definition_line, callers):
            break
    return callers


def find_callers(
    repo_root: str | Path,
    symbol: str,
    defining_file: str,
    definition_line: int = 0,
) -> list[CallerRef]:
    """
    Find internal *usages* of `symbol` under `repo_root`.

    Args:
        symbol: qualname of the changed symbol; for methods ("Client.request")
            the method name is matched, since call sites say `obj.request(...)`.
        defining_file: repo-relative path of the file that defines the symbol —
            its own `def`/`class` line is not a caller.
        definition_line: head-file line of the definition, excluded from matches
            within the defining file.
    """
    root = Path(repo_root)
    name = symbol.rsplit(".", 1)[-1]
    mats = _matchers(name)
    def_norm = defining_file.replace("\\", "/")

    callers: list[CallerRef] = []
    for path in _iter_py_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _scan_text(text, rel, name, mats, def_norm, definition_line, callers):
            break
    return callers
