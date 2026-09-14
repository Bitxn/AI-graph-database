"""
Execution verification — the reason a generated test is worth trusting.

Every model-written test is written to a temp file OUTSIDE the repo and run
with the repo's own interpreter and pytest. It is kept only if:
  1. pytest exits 0 (every test in the file passed, at least one collected), AND
  2. the coverage XML from that run shows at least one of the target gap
     lines actually executed.

Anything else is discarded. No exceptions — an unexecuted test is a guess.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from oneport_testgap.coverage_utils import parse_coverage_xml


@dataclass
class VerifyResult:
    passed: bool
    newly_covered: list[int] = field(default_factory=list)
    output: str = ""

    @property
    def verified(self) -> bool:
        """Passed AND actually exercised the gap lines it was written for."""
        return self.passed and bool(self.newly_covered)


def module_import_path(file_path: str) -> str:
    """
    Best-effort dotted import path for a repo-relative file, handed to the
    model so its imports resolve when we run the test with PYTHONPATH=repo.

      src/pkg/mod.py   ->  pkg.mod      (src/ layout)
      pkg/__init__.py  ->  pkg
      app.py           ->  app
    """
    norm = file_path.replace("\\", "/")
    if norm.startswith("src/"):
        norm = norm[len("src/"):]
    if norm.endswith(".py"):
        norm = norm[:-3]
    if norm.endswith("/__init__"):
        norm = norm[: -len("/__init__")]
    return norm.replace("/", ".")


def verify_test(
    code: str,
    repo_root: str | Path,
    target_file: str,
    gap_lines: list[int],
    timeout: int = 120,
) -> VerifyResult:
    """Run one generated test file in isolation and check the gap lines ran."""
    repo_root = Path(repo_root).resolve()
    tmpdir = Path(tempfile.mkdtemp(prefix="oneport-testgap-verify-"))
    test_path = tmpdir / "test_oneport_generated.py"
    xml_path = tmpdir / "coverage.xml"
    test_path.write_text(code, encoding="utf-8")

    env = dict(os.environ)
    extra_paths = [str(repo_root), str(repo_root / "src")]
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(p for p in [*extra_paths, existing] if p)

    cmd = [
        sys.executable, "-m", "pytest",
        str(test_path),
        "-q",
        "-p", "no:cacheprovider",
        "-o", "addopts=",          # the repo's own addopts must not interfere
        "--cov=.",
        f"--cov-report=xml:{xml_path}",
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return VerifyResult(passed=False, output=f"Timed out after {timeout}s.")

    output_tail = (result.stdout + "\n" + result.stderr).strip()[-2500:]
    # Exit 0 = all collected tests passed. 5 = nothing collected; 1 = failures.
    if result.returncode != 0:
        return VerifyResult(passed=False, output=output_tail)

    if not xml_path.exists():
        return VerifyResult(passed=True, output=output_tail)

    coverage = parse_coverage_xml(xml_path, repo_root)
    hits = coverage.get(target_file.replace("\\", "/"), {})
    newly_covered = sorted(line for line in gap_lines if hits.get(line, 0) > 0)
    return VerifyResult(passed=True, newly_covered=newly_covered, output=output_tail)
