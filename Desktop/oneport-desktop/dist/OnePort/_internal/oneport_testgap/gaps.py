"""
Gap model and the deterministic changed ∩ uncovered intersection.

A Gap is a set of lines in one function that were changed in the diff AND
never executed by the test suite. Everything in this module is computed from
the diff, coverage.xml and the Python AST — the LLM only ever adds risk labels
and explanations on top (see ranker.py).
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from oneport_testgap.coverage_utils import uncovered_lines
from oneport_testgap.diff_utils import path_matches


class Risk(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNRANKED = "unranked"


# Sort order for reports: most dangerous first.
RISK_ORDER = {
    Risk.CRITICAL: 0,
    Risk.HIGH: 1,
    Risk.MEDIUM: 2,
    Risk.LOW: 3,
    Risk.UNRANKED: 4,
}

# Report/gate thresholds: --min-risk high shows critical+high, etc.
_MIN_RISK_CUTOFF = {"high": 1, "medium": 2, "low": 4}


@dataclass
class Gap:
    """Changed-but-untested lines within one function (or module scope)."""

    file: str                      # repo-relative posix path
    function: str                  # qualified name, or "<module>"
    lines: list[int]               # sorted new-file line numbers
    snippet: str = ""              # source context shown to the model and the user
    measured: bool = True          # False when the file was absent from coverage data
    risk: Risk = Risk.UNRANKED     # filled by ranker.py
    why: str = ""                  # one-sentence risk explanation, filled by ranker.py
    waived: bool = False           # approved via .oneport/testgap-waivers.yml —
                                   # shown, but excluded from the CI gate.
    waiver_reason: str = ""

    @property
    def first_line(self) -> int:
        return self.lines[0] if self.lines else 0

    @property
    def last_line(self) -> int:
        return self.lines[-1] if self.lines else 0

    @property
    def line_ranges(self) -> str:
        """Human form: '12-15, 22' instead of [12, 13, 14, 15, 22]."""
        if not self.lines:
            return ""
        ranges: list[str] = []
        start = prev = self.lines[0]
        for n in self.lines[1:]:
            if n == prev + 1:
                prev = n
                continue
            ranges.append(f"{start}-{prev}" if prev > start else str(start))
            start = prev = n
        ranges.append(f"{start}-{prev}" if prev > start else str(start))
        return ", ".join(ranges)

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "function": self.function,
            "lines": self.lines,
            "line_ranges": self.line_ranges,
            "measured": self.measured,
            "risk": self.risk.value,
            "why": self.why,
            "waived": self.waived,
            "waiver_reason": self.waiver_reason,
        }


@dataclass
class GeneratedTest:
    """One model-written test and the verdict from actually running it."""

    gap: Gap
    code: str
    verified: bool = False
    path: str = ""                 # repo-relative path once written (verified only)
    attempts: int = 0
    newly_covered: list[int] = field(default_factory=list)
    failure_output: str = ""       # tail of pytest output for discarded tests

    def to_dict(self) -> dict:
        return {
            "file": self.gap.file,
            "function": self.gap.function,
            "verified": self.verified,
            "path": self.path,
            "attempts": self.attempts,
            "newly_covered": self.newly_covered,
        }


@dataclass
class GapReport:
    """Everything one `analyze` run learned, ready for a formatter."""

    target: str
    model: str = ""
    gaps: list[Gap] = field(default_factory=list)
    generated: list[GeneratedTest] = field(default_factory=list)
    changed_line_count: int = 0
    gap_line_count: int = 0
    files_analyzed: int = 0
    total_tokens: int = 0
    pr_ref: dict = field(default_factory=dict)

    @property
    def has_critical_gaps(self) -> bool:
        """CI gate (default threshold): any non-waived critical-risk gap."""
        return self.is_blocking("critical")

    def is_blocking(self, fail_on: str = "critical") -> bool:
        """CI gate at a tunable threshold: True if any NON-WAIVED gap is at or
        above ``fail_on`` (critical | high | medium | low). Waived gaps never
        block; UNRANKED gaps (rank above 'low') never block on their own."""
        cutoff = RISK_ORDER[Risk(fail_on)]
        return any(
            not g.waived and RISK_ORDER[g.risk] <= cutoff
            for g in self.gaps
        )

    def gaps_at_least(self, min_risk: str) -> list[Gap]:
        cutoff = _MIN_RISK_CUTOFF.get(min_risk, 4)
        return [g for g in self.gaps if RISK_ORDER[g.risk] <= cutoff]

    @property
    def waived_count(self) -> int:
        return sum(1 for g in self.gaps if g.waived)

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "model": self.model,
            "changed_line_count": self.changed_line_count,
            "gap_line_count": self.gap_line_count,
            "files_analyzed": self.files_analyzed,
            "total_tokens": self.total_tokens,
            "waived": self.waived_count,
            "gaps": [g.to_dict() for g in self.gaps],
            "generated_tests": [t.to_dict() for t in self.generated],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# ── Building gaps from the deterministic facts ─────────────────────────────────

_TEST_PATH_PATTERNS = [
    "tests/**", "**/tests/**", "test/**", "**/test/**",
    "**/test_*.py", "test_*.py", "**/*_test.py", "*_test.py",
    "**/conftest.py", "conftest.py",
]


def is_test_path(path: str) -> bool:
    """Test files don't need coverage of themselves — never gap candidates."""
    return path_matches(path, _TEST_PATH_PATTERNS)


def build_gaps(
    changed: dict[str, set[int]],
    coverage: dict[str, dict[int, int]],
    repo_root: str | Path,
    ignore_patterns: list[str] | None = None,
) -> tuple[list[Gap], dict]:
    """
    Intersect changed lines with uncovered lines and group by function.

    Files absent from the coverage data entirely (e.g. excluded from the run)
    are treated as fully uncovered — their executable changed lines all count,
    flagged with measured=False so the report is honest about it.

    Returns (gaps, stats) where stats feeds the report header.
    """
    repo_root = Path(repo_root).resolve()
    gaps: list[Gap] = []
    changed_total = 0
    gap_total = 0
    files_analyzed = 0

    for file_path in sorted(changed):
        norm = file_path.replace("\\", "/")
        if not norm.endswith(".py") or is_test_path(norm):
            continue
        if ignore_patterns and path_matches(norm, ignore_patterns):
            continue

        files_analyzed += 1
        changed_set = changed[file_path]
        changed_total += len(changed_set)
        source = _read_source(repo_root / norm)

        if norm in coverage:
            gap_lines = changed_set & uncovered_lines(coverage[norm])
            measured = True
        else:
            # Not measured at all — every executable changed line is a gap.
            gap_lines = changed_set & _executable_lines(source, changed_set)
            measured = False

        if not gap_lines:
            continue
        gap_total += len(gap_lines)
        gaps.extend(_group_by_function(norm, gap_lines, source, measured))

    stats = {
        "changed_line_count": changed_total,
        "gap_line_count": gap_total,
        "files_analyzed": files_analyzed,
    }
    return gaps, stats


def _read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _executable_lines(source: str, fallback: set[int]) -> set[int]:
    """Statement line numbers via AST; if the file can't be parsed (or wasn't
    readable), fall back to counting every changed line so nothing hides."""
    if not source:
        return set(fallback)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set(fallback)
    return {
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.stmt) and hasattr(node, "lineno")
    }


def _function_ranges(source: str) -> list[tuple[str, int, int]]:
    """[(qualname, start, end)] for every function/method in the file."""
    if not source:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    ranges: list[tuple[str, int, int]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                ranges.append((qualname, child.lineno, child.end_lineno or child.lineno))
                visit(child, f"{qualname}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return ranges


def _group_by_function(
    file_path: str, gap_lines: set[int], source: str, measured: bool
) -> list[Gap]:
    ranges = _function_ranges(source)
    by_function: dict[str, list[int]] = {}

    for line in sorted(gap_lines):
        # Innermost enclosing function = the smallest containing range.
        enclosing = [
            (end - start, name) for name, start, end in ranges if start <= line <= end
        ]
        name = min(enclosing)[1] if enclosing else "<module>"
        by_function.setdefault(name, []).append(line)

    range_map = {name: (start, end) for name, start, end in ranges}
    return [
        Gap(
            file=file_path,
            function=name,
            lines=lines,
            snippet=_snippet(source, range_map.get(name), lines),
            measured=measured,
        )
        for name, lines in by_function.items()
    ]


def _snippet(
    source: str,
    func_range: tuple[int, int] | None,
    gap_lines: list[int],
    max_lines: int = 80,
    context: int = 5,
) -> str:
    """Function source (numbered) when it fits, else gap lines ± context."""
    if not source:
        return ""
    src_lines = source.splitlines()

    if func_range and (func_range[1] - func_range[0]) < max_lines:
        start, end = func_range
    else:
        start = max(1, min(gap_lines) - context)
        end = min(len(src_lines), max(gap_lines) + context)

    start = max(1, start)
    end = min(len(src_lines), end)
    marks = set(gap_lines)
    return "\n".join(
        f"{n:>5}{'>' if n in marks else ' '} {src_lines[n - 1]}"
        for n in range(start, end + 1)
    )
