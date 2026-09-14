# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Repo walker — turns a directory tree into structured FileNodes + ModuleNodes.

Deterministic and dependency-free: filters to source files, skips the usual
noise (.git, node_modules, venv, build output), extracts top-level symbols,
imports, and a docstring/leading comment per file, then groups files into
modules by directory. No LLM here — this is the cheap structural pass the
hierarchical summarizer builds on.
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from oneport_context.models import FileNode, ModuleNode, Symbol
from oneport_context.scan import ScanConfig

_LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go", ".java": "Java",
    ".kt": "Kotlin", ".rb": "Ruby", ".rs": "Rust", ".php": "PHP", ".cs": "C#",
    ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++", ".swift": "Swift", ".scala": "Scala",
}


def language_of(path: Path, scan: ScanConfig | None = None) -> str | None:
    ext = path.suffix.lower()
    if scan and ext in scan.extra_extensions:
        return scan.extra_extensions[ext]
    return _LANG_BY_EXT.get(ext)


def _hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def walk_repo(root: Path, scan: ScanConfig | None = None) -> list[FileNode]:
    """Return FileNodes for every source file under root (structural pass, no LLM).

    ``scan`` controls what's looked at (ignore dirs/globs, .gitignore, extra
    languages, caps). When the file cap is hit, ``scan.truncated`` is set so the
    caller can be honest that the scan was partial."""
    root = root.resolve()
    scan = scan or ScanConfig()
    scan.truncated = False
    nodes: list[FileNode] = []
    for path in _iter_source_files(root, scan):
        lang = language_of(path, scan)
        if not lang:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        nodes.append(FileNode(
            path=rel,
            language=lang,
            loc=text.count("\n") + 1,
            summary=_leading_doc(text, lang),
            symbols=_symbols(text, lang),
            imports=_imports(text, lang),
            content_hash=_hash_text(text),
        ))
        if len(nodes) >= scan.max_files:
            scan.truncated = _has_more_source_files(root, scan, seen=len(nodes))
            break
    return nodes


def scan_signatures(root: Path, scan: ScanConfig | None = None) -> dict[str, str]:
    """Cheap freshness pass: {rel_path -> content_hash} without symbol parsing.

    Used to detect whether a repo changed since it was indexed, without paying
    for the full structural walk or any model call."""
    root = root.resolve()
    scan = scan or ScanConfig()
    sigs: dict[str, str] = {}
    for path in _iter_source_files(root, scan):
        if not language_of(path, scan):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sigs[path.relative_to(root).as_posix()] = _hash_text(text)
        if len(sigs) >= scan.max_files:
            break
    return sigs


def group_modules(files: list[FileNode]) -> list[ModuleNode]:
    """Group files into modules by directory, then wire module→module deps from imports."""
    buckets: dict[str, list[FileNode]] = {}
    for f in files:
        buckets.setdefault(_module_of(f.path), []).append(f)

    modules = [ModuleNode(name=name, files=sorted(fs, key=lambda x: x.path))
               for name, fs in sorted(buckets.items())]
    _wire_dependencies(modules)
    return modules


# --------------------------------------------------------------------------- #
# Internals                                                                     #
# --------------------------------------------------------------------------- #

def _iter_source_files(root: Path, scan: ScanConfig):
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            rel = e.relative_to(root).as_posix()
            if e.is_dir():
                if not scan.is_ignored_dir(e.name, rel):
                    stack.append(e)
            elif e.is_file():
                if scan.is_ignored_file(rel):
                    continue
                try:
                    if e.stat().st_size <= scan.max_file_bytes:
                        yield e
                except OSError:
                    continue


def _has_more_source_files(root: Path, scan: ScanConfig, seen: int) -> bool:
    """True if the scan would have yielded more than ``seen`` files — i.e. the
    cap genuinely truncated the walk (vs. landing exactly on the count)."""
    count = 0
    for path in _iter_source_files(root, scan):
        if language_of(path, scan):
            count += 1
            if count > seen:
                return True
    return False


# Directories that only add nesting depth without distinguishing features. A
# src-layout package (src/<pkg>/feature/…) would otherwise collapse into ONE giant
# module; peeling these exposes the real sub-structure (feature folders as modules).
_WRAPPER_DIRS = {"src", "lib", "sources", "source"}


def _module_of(rel_path: str) -> str:
    parts = rel_path.split("/")
    # Peel leading wrapper dirs, but never so far that the file has no directory
    # left (keep at least one dir + the filename to name a module).
    while len(parts) > 2 and parts[0].lower() in _WRAPPER_DIRS:
        parts = parts[1:]
    if len(parts) == 1:
        return "(root)"
    # Group two directories deep when still nested (monorepo layouts like
    # services/authz/… stay distinct), else one level.
    if len(parts) >= 3:
        return "/".join(parts[:2])
    return parts[0]


def _is_comment_fragment(s: str) -> bool:
    """True for a comment that's a code lead-in, not a description.

    `docs_src/security` opens with `# to get a string like this run:` above an
    `openssl rand -hex 32` command; that colon-terminated fragment became the
    module summary. Reject lead-ins (end punctuation that expects a following
    line) and near-empty comments.
    """
    return s.endswith((":", "=", "{", "(", "[", ",", "\\")) or len(s.split()) < 3


# Tooling pragmas that open a lot of TS/JS files — not descriptions. hono's
# summaries were littered with "eslint-disable ...", "@jsxImportSource ../ */".
_DIRECTIVE_MARKERS = (
    "eslint-disable", "eslint-enable", "prettier-ignore", "ts-expect-error",
    "ts-ignore", "ts-nocheck", "jsximportsource", "istanbul ignore", "c8 ignore",
    "@license", "@preserve", "@flow", "@ts-",
)


def _is_directive_comment(cleaned: str) -> bool:
    """True for a linter/compiler pragma or a bare JSDoc tag, not prose."""
    low = cleaned.lower()
    return cleaned.startswith("@") or any(m in low for m in _DIRECTIVE_MARKERS)


def _leading_doc(text: str, lang: str) -> str:
    """The file's MODULE docstring, or a leading comment line — a cheap summary.

    Python uses ast.get_docstring, which returns ONLY the module docstring. The
    old regex ran with re.MULTILINE and matched the first *function* docstring
    when a module had none — so fastapi/openapi's summary became one helper's
    "Serialize a value to JSON..." instead of anything about the module.
    """
    if lang == "Python":
        try:
            doc = ast.get_docstring(ast.parse(text))
        except (SyntaxError, ValueError, RecursionError):
            doc = None
        if doc:
            for line in doc.strip().splitlines():
                if line.strip():
                    return line.strip()[:200]
        # No module docstring → fall through to the comment scan below.

    # Comment markers are language-specific. Python comments are ONLY `#`; its
    # `*`/`//`/`/*` never start a comment. Treating them as one made a Python
    # keyword-only signature line (`    *, client: TestClient, ...`) get read as
    # a JSDoc comment and become the file summary. C-family langs keep `*`//`/*`.
    comment_prefixes = ("#",) if lang == "Python" else ("//", "#", "*", "/*")
    for line in text.splitlines()[:15]:
        s = line.strip()
        if s.startswith(comment_prefixes) and len(s) > 4:
            cleaned = s.lstrip("/#*!/ ").strip()
            cleaned = re.sub(r"\s*\*+/?\s*$", "", cleaned).strip()  # drop trailing */
            if (cleaned
                    and not cleaned.lower().startswith(("copyright", "spdx", "license"))
                    and not _is_directive_comment(cleaned)
                    and not _is_comment_fragment(cleaned)):
                return cleaned[:200]
    return ""


_SYMBOL_PATTERNS = {
    "Python": [
        (re.compile(r"^\s*class\s+(\w+)", re.M), "class"),
        (re.compile(r"^\s*(?:async\s+)?def\s+(\w+)", re.M), "function"),
    ],
    "JavaScript": [
        (re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.M), "class"),
        (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.M), "function"),
        (re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", re.M), "function"),
    ],
    "Go": [
        (re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)", re.M), "function"),
        (re.compile(r"^\s*type\s+(\w+)\s+struct", re.M), "class"),
    ],
    "Java": [
        (re.compile(r"^\s*(?:public|private|protected)?\s*(?:final\s+)?class\s+(\w+)", re.M), "class"),
        (re.compile(r"^\s*(?:public|private|protected)\s+[\w<>\[\]]+\s+(\w+)\s*\(", re.M), "method"),
    ],
}
_SYMBOL_PATTERNS["TypeScript"] = _SYMBOL_PATTERNS["JavaScript"]
_SYMBOL_PATTERNS["Kotlin"] = [
    (re.compile(r"^\s*(?:data\s+)?class\s+(\w+)", re.M), "class"),
    (re.compile(r"^\s*(?:suspend\s+)?fun\s+(\w+)", re.M), "function"),
]


def _symbols(text: str, lang: str) -> list[Symbol]:
    patterns = _SYMBOL_PATTERNS.get(lang, [])
    out: list[Symbol] = []
    for pat, kind in patterns:
        for m in pat.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            out.append(Symbol(name=m.group(1), kind=kind, line=line))
    out.sort(key=lambda s: s.line)
    return out[:60]


_IMPORT_PATTERNS = {
    "Python": re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M),
    "Go": re.compile(r'"([^"]+)"'),
    "Java": re.compile(r"^\s*import\s+([\w.]+)", re.M),
}
# `[^'"\n]` (not `[^'"]`) so the match can't span newlines: without the \n
# guard, an `import ... from '...'` INSIDE a JSDoc @example block ran on across
# lines and captured the whole comment as one "import" (hono/combine's imports
# held a 4-line `app.use(...)` blob). A module specifier is a single-line token.
_JS_IMPORT = re.compile(
    r"""(?:import[^'"\n]*from\s+|require\(\s*|import\(\s*)['"]([^'"\n]+)['"]"""
)
# A real module specifier has no whitespace or comment/code punctuation.
_BAD_SPECIFIER = re.compile(r"[\s*(){}]|//|/\*")
_IMPORT_PATTERNS["JavaScript"] = _JS_IMPORT
_IMPORT_PATTERNS["TypeScript"] = _JS_IMPORT
_IMPORT_PATTERNS["Kotlin"] = re.compile(r"^\s*import\s+([\w.]+)", re.M)


def _imports(text: str, lang: str) -> list[str]:
    pat = _IMPORT_PATTERNS.get(lang)
    if not pat:
        return []
    found: list[str] = []
    for m in pat.finditer(text):
        target = next((g for g in m.groups() if g), None)
        if lang == "Go":
            # Go's pattern is greedy on any quoted string; only keep import-block lines.
            if "/" not in target and "." not in target:
                continue
        # Drop anything that isn't a clean specifier — catches import-like text
        # captured out of comments/example code (whitespace, `//`, `(`, etc.).
        if lang in ("JavaScript", "TypeScript") and _BAD_SPECIFIER.search(target):
            continue
        if target:
            found.append(target)
    # de-dupe, cap
    seen: list[str] = []
    for t in found:
        if t not in seen:
            seen.append(t)
    return seen[:40]


def _wire_dependencies(modules: list[ModuleNode]) -> None:
    """Map each module's imports to other modules (local imports only)."""
    names = {m.name for m in modules}
    for m in modules:
        deps: set[str] = set()
        for f in m.files:
            for imp in f.imports:
                seg = imp.replace(".", "/").strip("/").split("/")
                for cand in ("/".join(seg[:2]), seg[0] if seg else ""):
                    if cand and cand in names and cand != m.name:
                        deps.add(cand)
        m.depends_on = sorted(deps)
