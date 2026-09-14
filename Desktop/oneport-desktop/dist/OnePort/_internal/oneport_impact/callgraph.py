"""
Reverse call graph via the `ast` module — deterministic, no model, no execution.

We walk every `.py` file once and record two things:
  * definitions   — every function / method / class, with its module-qualified name;
  * call sites    — every place that calls something, indexed by the called name.

`callers_of(symbol)` then answers "who calls this?" by looking the symbol's simple
name up in the call-site index.

HONEST LIMITS (stated, never hidden):
  * Resolution is by NAME, not by full type inference — Python is too dynamic to
    resolve every call statically without running it. So `callers_of("charge")`
    returns every site that calls something named `charge`. When two symbols share
    a name, callers of both are returned; the report says so. This is the same
    trade-off `grep`-based "find usages" makes, but structured (we know the caller
    function and skip strings/comments), so it is strictly better than grep and
    never worse.
  * Dynamic dispatch (getattr, decorators that rename, monkeypatching) is invisible
    to any static tool; we never pretend otherwise.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path

from oneport_impact.result import CallSite, Symbol

_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".tox", ".idea", ".ruff_cache", "site-packages",
}

# Frozen history: a Django/Alembic migration records a schema change that ALREADY
# RAN. Editing a function today cannot break a migration authored in 2017 — it
# will never execute against the new code. Counting them as call sites inflated
# saleor's blast radius with 2017/2018 migrations and made fan-in meaningless on
# exactly the mature repos where blast radius matters most.
#
# Unlike _SKIP_DIRS (build artifacts nobody wants reported), this IS the user's
# own code, so the count is surfaced as a note rather than dropped silently.
_FROZEN_DIRS = {"migrations", "versions"}
_MAX_FILE_BYTES = 1_500_000


def _module_name(rel_path: str) -> str:
    """repo-relative path → dotted module, e.g. billing/stripe.py -> billing.stripe."""
    p = rel_path.replace(os.sep, "/")
    if p.endswith(".py"):
        p = p[:-3]
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".")


@dataclass
class CallGraph:
    """Definitions and reverse call sites for a whole repo."""
    root: Path
    defs_by_name: dict[str, list[Symbol]] = field(default_factory=dict)
    callers_by_name: dict[str, list[CallSite]] = field(default_factory=dict)
    files_parsed: int = 0
    parse_errors: list[str] = field(default_factory=list)
    # Migration files excluded from the graph (frozen history — see _FROZEN_DIRS).
    frozen_files_skipped: int = 0

    # ── queries ──────────────────────────────────────────────────────────────
    def all_symbols(self) -> list[Symbol]:
        return [s for group in self.defs_by_name.values() for s in group]

    def symbols_in_file(self, rel_path: str) -> list[Symbol]:
        rel = rel_path.replace(os.sep, "/")
        return [s for s in self.all_symbols() if s.file == rel]

    def resolve(self, query: str) -> list[Symbol]:
        """
        Resolve a symbol query to definition sites. Accepts:
          * a simple name           "charge"
          * a qualified name        "billing.stripe.Client.charge"  (suffix match)
          * a file:line             "billing/stripe.py:42"
        """
        query = query.strip()
        if ":" in query and query.rsplit(":", 1)[1].isdigit():
            path, lineno = query.rsplit(":", 1)
            path = path.replace(os.sep, "/")
            line = int(lineno)
            # nearest def at or above the line in that file
            here = sorted(
                (s for s in self.all_symbols() if s.file == path and s.line <= line),
                key=lambda s: s.line,
            )
            return [here[-1]] if here else []

        simple = query.rsplit(".", 1)[-1]
        candidates = self.defs_by_name.get(simple, [])
        if "." in query:
            # qualname suffix match, e.g. "Client.charge" matches "a.b.Client.charge"
            exact = [s for s in candidates if s.qualname == query or s.qualname.endswith("." + query)]
            if exact:
                return exact
        return list(candidates)

    def name_def_files(self, name: str) -> int:
        """How many distinct files define this name (its ambiguity)."""
        return len({s.file for s in self.defs_by_name.get(name, [])})

    def attributable(self, symbol: Symbol, max_ambiguity: int = 4) -> bool:
        """
        Can call sites to `symbol.name` be trusted to mean THIS symbol?

        Name-based resolution is only sound when the name is distinctive. A dunder
        (__init__, __str__) or a name defined in many files (get, filter, save) is
        called from everywhere and would wildly inflate fan-in, so we refuse to
        attribute it rather than report a number we can't defend. This is the line
        between "structured, better than grep" and "confidently wrong".
        """
        name = symbol.name
        if name.startswith("__") and name.endswith("__"):
            return False
        return self.name_def_files(name) <= max_ambiguity

    def callers_of(self, symbol: Symbol, max_ambiguity: int = 4) -> list[CallSite]:
        """Call sites reaching `symbol`, by its simple name. Returns [] for names
        too ambiguous to attribute (see `attributable`). A symbol never counts
        itself (recursive calls inside its own body are not external impact)."""
        if not self.attributable(symbol, max_ambiguity):
            return []
        sites = self.callers_by_name.get(symbol.name, [])
        return [c for c in sites if not _within(symbol, c)]


def _within(symbol: Symbol, site: CallSite) -> bool:
    """True if the call site is inside the symbol itself (self/recursive call)."""
    return site.file == symbol.file and site.caller_qualname.endswith(symbol.name)


# ── building ─────────────────────────────────────────────────────────────────────

class _FileVisitor(ast.NodeVisitor):
    """Collect defs + call sites from one module, tracking the enclosing scope."""

    def __init__(self, module: str, rel_path: str) -> None:
        self.module = module
        self.rel = rel_path
        self.scope: list[str] = [module]      # qualname stack
        self.defs: list[Symbol] = []
        self.calls: list[CallSite] = []

    # -- definitions --
    def _enter_def(self, node, kind: str) -> None:
        # scope[-1] is already the fully-qualified parent (module or parent def),
        # so the qualname is just parent + name — never re-join the whole stack.
        qual = f"{self.scope[-1]}.{node.name}"
        # a function directly under a class is a method
        real_kind = "method" if (kind == "function" and self._in_class()) else kind
        self.defs.append(Symbol(
            name=node.name, qualname=qual, file=self.rel, line=node.lineno, kind=real_kind,
        ))
        self.scope.append(qual)
        self.generic_visit(node)
        self.scope.pop()

    def _in_class(self) -> bool:
        # crude but effective: the immediate scope frame is a ClassDef qualname we set
        return getattr(self, "_class_depth", 0) > 0

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_depth = getattr(self, "_class_depth", 0) + 1
        self._enter_def(node, "class")
        self._class_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter_def(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter_def(node, "function")

    # -- call sites --
    def visit_Call(self, node: ast.Call) -> None:
        name = _called_name(node.func)
        if name:
            self.calls.append(CallSite(
                caller_qualname=self.scope[-1],
                file=self.rel,
                line=getattr(node, "lineno", 0),
                called=name,
            ))
        self.generic_visit(node)


def _called_name(func: ast.expr) -> str:
    """The bare name being called: foo() -> 'foo', obj.foo() -> 'foo'."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _iter_py_files(root: Path):
    """Yield (path, is_frozen) for every .py file worth parsing.

    Frozen files are yielded rather than dropped so the caller can COUNT them —
    a silent exclusion of the user's own code is how a tool ends up quietly
    lying about its coverage.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        parts = Path(dirpath).parts
        is_frozen = any(p in _FROZEN_DIRS for p in parts)
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name, is_frozen


def build_call_graph(root: str | Path, max_files: int = 20_000) -> CallGraph:
    """Parse every `.py` under `root` into a CallGraph. Never raises on a single
    unparseable file — it's recorded in `parse_errors` and skipped."""
    root = Path(root).resolve()
    graph = CallGraph(root=root)

    for fpath, is_frozen in _iter_py_files(root):
        if is_frozen:
            graph.frozen_files_skipped += 1
            continue
        if graph.files_parsed >= max_files:
            graph.parse_errors.append(f"stopped after {max_files} files (repo too large)")
            break
        try:
            raw = fpath.read_bytes()
        except OSError:
            continue
        if len(raw) > _MAX_FILE_BYTES:
            continue
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        rel = str(fpath.relative_to(root)).replace(os.sep, "/")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            graph.parse_errors.append(f"{rel}: {exc.msg} (line {exc.lineno})")
            continue

        visitor = _FileVisitor(_module_name(rel), rel)
        visitor.visit(tree)
        graph.files_parsed += 1

        for sym in visitor.defs:
            graph.defs_by_name.setdefault(sym.name, []).append(sym)
        for call in visitor.calls:
            graph.callers_by_name.setdefault(call.called, []).append(call)

    return graph
