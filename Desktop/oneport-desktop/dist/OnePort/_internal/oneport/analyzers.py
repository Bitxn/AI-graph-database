"""
Static-analyzer fusion — run ruff and bandit alongside the model.

LLMs are strong on semantics and weak on exhaustiveness; linters are the
opposite. When ruff/bandit are installed, their findings on the changed files
are fed into the review prompt as *verification hints*: the model confirms
real ones (with proper severity, explanation, and a fix), drops false
positives, and catches what the linters can't see. That fusion point is also
the dedup point — we never blindly append raw linter output to the results.

Runs only for local reviews (single file, --staged, --head) where the files
exist on disk. Everything here is best-effort: missing tools, crashes, and
timeouts silently produce zero hints, never a failed review.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_TIMEOUT_S = 30


@dataclass
class AnalyzerFinding:
    tool: str        # "ruff" | "bandit"
    file: str
    line: int
    code: str        # e.g. "F841", "B602"
    message: str


def run_analyzers(paths: list[str], cwd: str | None = None) -> list[AnalyzerFinding]:
    """Run every available analyzer over the Python files in `paths`."""
    py_paths = [p for p in paths if p.endswith(".py") and Path(cwd or ".", p).exists()]
    if not py_paths:
        return []

    findings: list[AnalyzerFinding] = []
    if shutil.which("ruff"):
        findings += _run_ruff(py_paths, cwd)
    if shutil.which("bandit"):
        findings += _run_bandit(py_paths, cwd)
    return findings


def format_findings_for_prompt(findings: list[AnalyzerFinding]) -> str:
    """Render analyzer hints for the review prompt."""
    if not findings:
        return ""
    lines = [
        "Static analyzer findings on these files (hints, not ground truth — "
        "confirm the real ones with proper severity and a fix, drop false "
        "positives, and do not simply repeat them verbatim):"
    ]
    for f in findings[:100]:  # cap: a misconfigured linter shouldn't flood the prompt
        lines.append(f"  [{f.tool}:{f.code}] {f.file}:{f.line} — {f.message}")
    return "\n".join(lines)


# ── Tool runners ────────────────────────────────────────────────────────────────

def _run_ruff(paths: list[str], cwd: str | None) -> list[AnalyzerFinding]:
    out = _run_json(["ruff", "check", "--output-format", "json", "--exit-zero", *paths], cwd)
    if not isinstance(out, list):
        return []
    return [
        AnalyzerFinding(
            tool="ruff",
            file=item.get("filename", ""),
            line=(item.get("location") or {}).get("row", 0),
            code=item.get("code") or "",
            message=item.get("message", ""),
        )
        for item in out
        if item.get("code")  # syntax-error entries have code=None
    ]


def _run_bandit(paths: list[str], cwd: str | None) -> list[AnalyzerFinding]:
    out = _run_json(["bandit", "-f", "json", "-q", *paths], cwd)
    if not isinstance(out, dict):
        return []
    return [
        AnalyzerFinding(
            tool="bandit",
            file=item.get("filename", ""),
            line=item.get("line_number", 0),
            code=item.get("test_id", ""),
            message=item.get("issue_text", ""),
        )
        for item in out.get("results", [])
    ]


def _run_json(cmd: list[str], cwd: str | None):
    """Run a tool and parse its JSON stdout; any failure returns None."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            # Analyzer JSON is UTF-8 and can carry unicode code snippets; without
            # an explicit encoding a cp1252 decode crash on Windows leaves stdout
            # =None, and the AttributeError from None.strip() is NOT in the except
            # below — it would take down the whole review.
            encoding="utf-8", errors="replace",
            timeout=_TIMEOUT_S, cwd=cwd,
        )
        return json.loads(proc.stdout) if (proc.stdout or "").strip() else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
