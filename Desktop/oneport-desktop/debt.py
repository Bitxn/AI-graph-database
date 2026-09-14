"""OnePort Desktop — technical-debt scanner (deterministic, offline, no tokens).

Surfaces the concrete, countable forms of technical debt in ANY repo:
  • debt markers      TODO / FIXME / HACK / XXX / BUG / DEPRECATED / REFACTOR
  • oversized files    files past a line threshold
  • long functions     functions/methods past a line threshold (heuristic)

It also folds in the latest test-gap and dependency signals from the project's
last scan, so "check my technical debt" is one place instead of three.

Nothing here calls an LLM or the network — it works with no internet and never
fabricates a finding: every item points at a real file:line. The long-function
detection is an honest heuristic (indentation for Python, brace-matching for
C-family/JS) and is labelled as such in the UI.
"""
from __future__ import annotations

import os
import re

try:  # reuse the app's ignore list so this matches every other scan
    from projects import IGNORE_DIRS
except Exception:  # pragma: no cover - fallback if imported standalone
    IGNORE_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv",
                   "dist", "build", "target", ".next", "coverage"}

MARKERS = ("TODO", "FIXME", "HACK", "XXX", "BUG", "DEPRECATED", "REFACTOR")
_MARKER_RE = re.compile(r"\b(" + "|".join(MARKERS) + r")\b")

CODE_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".java",
    ".rb", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".php", ".swift",
    ".kt", ".kts", ".scala", ".sh", ".bash", ".sql", ".vue", ".svelte",
    ".html", ".css", ".scss", ".less", ".yaml", ".yml", ".toml",
}
BRACE_EXT = {
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".java", ".rs",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".php", ".swift", ".kt",
    ".kts", ".scala",
}

FILE_MAX_LINES = 400        # a file past this counts as "oversized"
FUNC_MAX_LINES = 60         # a function past this counts as "long"
MAX_BYTES = 1_500_000       # skip bigger files (generated / minified)
MAX_ITEMS = 400             # cap per category so the UI + AI prompt stay sane

_BRACE_HDR = re.compile(
    r"(?:function\s+([A-Za-z_$][\w$]*)|"          # function foo(
    r"([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:=>\s*\{|\{))"  # foo(...) {  /  foo = (...) => {
)
_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "else", "do",
             "try", "function", "with", "throw"}


def _iter_files(root: str):
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs
                   if d not in IGNORE_DIRS and not d.startswith(".")]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in CODE_EXT:
                yield os.path.join(dp, f), ext


def _read(path: str):
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read().splitlines()
    except OSError:
        return None


def _py_functions(lines):
    """(name, start_line, length) for Python defs via indentation — reliable."""
    out, n = [], len(lines)
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)(?:async\s+)?def\s+([A-Za-z_]\w*)", ln)
        if not m:
            continue
        indent, j, last = len(m.group(1)), i + 1, i
        while j < n:
            s = lines[j]
            if s.strip():
                cur = len(s) - len(s.lstrip())
                if cur <= indent:
                    break
                last = j
            j += 1
        out.append((m.group(2), i + 1, last - i + 1))
    return out


def _brace_functions(lines):
    """(name, start_line, length) for C-family/JS — brace matching. Heuristic."""
    out, n, i = [], len(lines), 0
    while i < n:
        ln = lines[i]
        stripped = ln.strip()
        first = stripped.split("(", 1)[0].strip().split()[-1:] if "(" in stripped else []
        if ("{" in ln and _BRACE_HDR.search(ln)
                and not stripped.startswith(("//", "*", "/*", "#"))
                and (not first or first[0] not in _KEYWORDS)):
            depth = ln.count("{") - ln.count("}")
            if depth <= 0:
                i += 1
                continue
            mm = _BRACE_HDR.search(ln)
            name = (mm.group(1) or mm.group(2)) if mm else None
            j = i + 1
            while j < n and depth > 0:
                depth += lines[j].count("{") - lines[j].count("}")
                j += 1
            out.append((name or "(anonymous)", i + 1, j - i))
            i = j
        else:
            i += 1
    return out


def scan(root: str, history_gates=None) -> dict:
    markers, oversized, longfns, files_scanned = [], [], [], 0
    for path, ext in _iter_files(root):
        lines = _read(path)
        if lines is None:
            continue
        files_scanned += 1
        rel = os.path.relpath(path, root).replace("\\", "/")

        for i, ln in enumerate(lines):
            m = _MARKER_RE.search(ln)
            if m and len(markers) < MAX_ITEMS:
                markers.append({"file": rel, "line": i + 1, "kind": m.group(1),
                                "text": ln.strip()[:160]})

        if len(lines) > FILE_MAX_LINES:
            oversized.append({"file": rel, "lines": len(lines)})

        try:
            if ext == ".py":
                fns = _py_functions(lines)
            elif ext in BRACE_EXT:
                fns = _brace_functions(lines)
            else:
                fns = []
        except Exception:
            fns = []
        for name, start, length in fns:
            if length > FUNC_MAX_LINES and len(longfns) < MAX_ITEMS:
                longfns.append({"file": rel, "line": start,
                                "name": name, "lines": length})

    oversized.sort(key=lambda x: -x["lines"])
    longfns.sort(key=lambda x: -x["lines"])
    marker_counts: dict[str, int] = {}
    for m in markers:
        marker_counts[m["kind"]] = marker_counts.get(m["kind"], 0) + 1

    total = len(markers) + len(oversized) + len(longfns)
    return {
        "files_scanned": files_scanned,
        "markers": markers,
        "marker_counts": marker_counts,
        "oversized": oversized,
        "long_functions": longfns,
        "folded": _fold(history_gates),
        "counts": {"markers": len(markers), "oversized": len(oversized),
                   "long_functions": len(longfns)},
        "total": total,
        "load": _load_label(total),
        "thresholds": {"file_lines": FILE_MAX_LINES, "func_lines": FUNC_MAX_LINES},
    }


def _fold(history_gates):
    """Pull the latest test-gap + dependency signals so debt is one view."""
    out = {}
    for g in (history_gates or []):
        gid = g.get("gate")
        if gid in ("test-gaps", "dependencies"):
            out[gid] = {"status": g.get("status"), "summary": g.get("summary", ""),
                        "title": g.get("title", gid)}
    return out


def _load_label(total: int) -> str:
    if total == 0:
        return "clean"
    if total < 15:
        return "light"
    if total <= 50:
        return "moderate"
    return "heavy"


def fix_gate_entry(result: dict) -> dict:
    """Build a synthetic gate_entry so the existing autofix flow can act on debt."""
    c = result.get("counts", {})
    top = []
    for m in result.get("markers", [])[:20]:
        top.append({"title": f"{m['kind']}: {m['text']}",
                    "file": m["file"], "line": m["line"]})
    for fn in result.get("long_functions", [])[:12]:
        top.append({"title": f"long function {fn['name']} ({fn['lines']} lines)",
                    "file": fn["file"], "line": fn["line"]})
    for o in result.get("oversized", [])[:8]:
        top.append({"title": f"oversized file ({o['lines']} lines)",
                    "file": o["file"], "line": 1})
    summary = (f"{result.get('total', 0)} technical-debt items — "
               f"{c.get('markers', 0)} markers, {c.get('oversized', 0)} oversized "
               f"files, {c.get('long_functions', 0)} long functions")
    return {"gate": "tech-debt", "title": "Technical debt", "status": "block",
            "summary": summary, "data": {"findings": top}}
