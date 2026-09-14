"""
Deterministic coverage facts: run the test suite under coverage (or consume an
existing coverage.xml) and answer, per file, which lines executed.

The LLM never touches this layer — coverage numbers come from pytest-cov's
Cobertura XML, full stop.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from oneport_testgap.exceptions import CoverageError


def missing_tooling() -> str | None:
    """Return a human message if pytest / pytest-cov aren't importable, else None.

    The CLI turns this into a graceful no-op instead of a stack trace.
    """
    missing = [
        name for name, module in (("pytest", "pytest"), ("pytest-cov", "pytest_cov"))
        if importlib.util.find_spec(module) is None
    ]
    if not missing:
        return None
    return (
        f"{' and '.join(missing)} not installed — cannot compute coverage. "
        f"Install with: pip install pytest pytest-cov "
        f"(or pass --coverage-file with an existing coverage.xml)."
    )


def run_coverage(
    repo_root: str | Path,
    pytest_args: list[str] | None = None,
    timeout: int = 600,
) -> Path:
    """
    Run the repo's test suite under coverage and return the coverage.xml path.

    The XML lands in a temp directory so nothing is written into the repo.
    `-o addopts=` neutralises the repo's own pytest addopts (which often carry
    their own --cov/--cov-fail-under flags that would fight ours).

    Exit codes 0 (all passed), 1 (some failed) and 5 (no tests collected) all
    still produce valid coverage data — a failing or empty suite is exactly
    when test gaps matter, so none of them abort the analysis.
    """
    repo_root = Path(repo_root).resolve()
    xml_path = Path(tempfile.mkdtemp(prefix="oneport-testgap-cov-")) / "coverage.xml"

    cmd = [
        sys.executable, "-m", "pytest",
        "-q",
        "-p", "no:cacheprovider",
        "-o", "addopts=",
        "--cov=.",
        f"--cov-report=xml:{xml_path}",
        *(pytest_args or []),
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CoverageError(f"Coverage run timed out after {timeout}s.") from exc

    if not xml_path.exists():
        tail = (result.stdout + "\n" + result.stderr).strip()[-1500:]
        raise CoverageError(
            f"pytest --cov produced no coverage.xml (exit {result.returncode}).\n{tail}"
        )
    return xml_path


def parse_coverage_xml(
    xml_path: str | Path,
    repo_root: str | Path,
) -> dict[str, dict[int, int]]:
    """
    Parse a Cobertura coverage.xml into {repo-relative posix path: {line: hits}}.

    Filenames in the XML are relative to the <sources> roots; each is resolved
    against every source root and re-relativised to repo_root, so keys line up
    with the paths a git diff uses. Files outside repo_root keep their XML
    filename as-is.
    """
    repo_root = Path(repo_root).resolve()
    try:
        tree = ET.parse(str(xml_path))
    except (ET.ParseError, OSError) as exc:
        raise CoverageError(f"Cannot read coverage XML at {xml_path}: {exc}") from exc

    root = tree.getroot()
    sources = [s.text for s in root.iter("source") if s.text]

    result: dict[str, dict[int, int]] = {}
    for cls in root.iter("class"):
        filename = cls.get("filename", "")
        if not filename:
            continue
        rel = _relativize(filename, sources, repo_root)

        lines = result.setdefault(rel, {})
        for line in cls.iter("line"):
            try:
                number = int(line.get("number", "0"))
                hits = int(float(line.get("hits", "0")))
            except ValueError:
                continue
            if number > 0:
                # A file can appear as several <class> entries — keep max hits.
                lines[number] = max(lines.get(number, 0), hits)
    return result


def uncovered_lines(line_hits: dict[int, int]) -> set[int]:
    """Executable lines with zero hits."""
    return {line for line, hits in line_hits.items() if hits == 0}


def _relativize(filename: str, sources: list[str], repo_root: Path) -> str:
    candidate = Path(filename)
    paths = [candidate] if candidate.is_absolute() else [
        Path(src) / filename for src in sources
    ] + [repo_root / filename]

    for p in paths:
        try:
            resolved = p.resolve()
        except OSError:
            continue
        if resolved.exists():
            try:
                return resolved.relative_to(repo_root).as_posix()
            except ValueError:
                return resolved.as_posix()
    # File not on disk (e.g. parsing someone else's coverage.xml) — normalise
    # separators and hope the XML-relative path matches the diff's.
    return filename.replace("\\", "/").replace(os.sep, "/")
