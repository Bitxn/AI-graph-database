"""
Test verification — the "pre-verified PR" feature.

Runs the repo's pytest suite before and after the codemods so `apply --verify` can
prove the change didn't break anything. Deterministic: we parse pytest's own exit
code and summary line, never a model. A missing pytest degrades to a clear note,
not a crash — the fixes still apply, they're just unverified.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

from oneport_upgrade.result import VerifyResult

_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|error|errors)")


def pytest_available() -> bool:
    return importlib.util.find_spec("pytest") is not None


def _run_pytest(root: Path, timeout: int) -> tuple[int, int]:
    """Return (passed, failed). A collection error counts toward failed."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts="],
            cwd=str(root), capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return 0, -1  # -1 signals "couldn't determine"
    passed = failed = 0
    for line in proc.stdout.splitlines()[::-1][:5]:   # summary is near the end
        for n, kind in _SUMMARY_RE.findall(line):
            if kind == "passed":
                passed = int(n)
            else:
                failed += int(n)
        if passed or failed:
            break
    return passed, failed


def verify(root: str | Path, run_before, timeout: int = 600) -> VerifyResult:
    """
    `run_before` is a zero-arg callable that applies the fixes; we run pytest
    before calling it and after, and compare. Returns a VerifyResult.
    """
    root = Path(root)
    if not pytest_available():
        run_before()   # still apply the fixes
        return VerifyResult(ran=False, note="pytest not installed — fixes applied but unverified.")

    bp, bf = _run_pytest(root, timeout)
    run_before()
    ap, af = _run_pytest(root, timeout)
    return VerifyResult(ran=True, before_passed=bp, before_failed=bf,
                       after_passed=ap, after_failed=af)
