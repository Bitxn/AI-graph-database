"""
Deterministic usage-evidence collection.

For each vulnerable package we grep the codebase for import statements and
call sites, recording file:line snippets. This evidence — not the model's
imagination — is what the triage layer reasons over. If a package is never
imported, triage is decided right here without an LLM call.
"""

from __future__ import annotations

import re
from pathlib import Path

from oneport_depcheck.result import UsageEvidence

# PyPI distribution name → import name, where they differ.
PY_IMPORT_NAMES = {
    "pillow": "PIL",
    "beautifulsoup4": "bs4",
    "pyyaml": "yaml",
    "scikit-learn": "sklearn",
    "opencv-python": "cv2",
    "python-dateutil": "dateutil",
    "pyjwt": "jwt",
    "python-dotenv": "dotenv",
    "msgpack-python": "msgpack",
    "protobuf": "google.protobuf",
    "attrs": "attr",
    "setuptools": "setuptools",
    "pycryptodome": "Crypto",
    "pyopenssl": "OpenSSL",
    "python-jose": "jose",
    "djangorestframework": "rest_framework",
    "flask-sqlalchemy": "flask_sqlalchemy",
    "psycopg2-binary": "psycopg2",
    "mysqlclient": "MySQLdb",
}

SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env", "node_modules",
    ".tox", "dist", "build", ".eggs", ".mypy_cache", ".pytest_cache",
    ".oneport", ".history",
}

# Paths whose usage counts as dev/test-only context, not runtime exposure.
DEV_PATH_MARKERS = ("test", "tests", "conftest", "spec", "fixtures", "examples")

MAX_FILE_BYTES = 1_000_000


def import_names_for(package: str, ecosystem: str) -> list[str]:
    """Candidate module names a package is imported as."""
    lower = package.lower()
    if ecosystem == "npm":
        return [package]  # npm packages are required by their registry name
    names = [lower.replace("-", "_")]
    if lower in PY_IMPORT_NAMES:
        names.insert(0, PY_IMPORT_NAMES[lower])
    if "." not in lower and "-" not in lower and lower not in names:
        names.append(lower)
    return names


def _python_patterns(module: str) -> list[re.Pattern]:
    escaped = re.escape(module)
    return [
        re.compile(rf"^\s*import\s+{escaped}(?=[\s.,]|$)", re.MULTILINE),
        re.compile(rf"^\s*from\s+{escaped}(?=[\s.])", re.MULTILINE),
        re.compile(rf"(?<![\w.]){escaped}\.\w+\s*\("),
    ]


def _js_patterns(module: str) -> list[re.Pattern]:
    escaped = re.escape(module)
    return [
        re.compile(rf"""require\(\s*['"]{escaped}(?:/[^'"]*)?['"]\s*\)"""),
        re.compile(rf"""from\s+['"]{escaped}(?:/[^'"]*)?['"]"""),
        re.compile(rf"""import\s*\(\s*['"]{escaped}(?:/[^'"]*)?['"]\s*\)"""),
    ]


def _iter_source_files(root: Path, suffixes: tuple[str, ...]):
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                    stack.append(entry)
            elif entry.suffix in suffixes:
                yield entry


def is_dev_path(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").lower().split("/")
    stem = parts[-1].rsplit(".", 1)[0]
    return (
        any(p in DEV_PATH_MARKERS for p in parts[:-1])
        or stem.startswith("test_")
        or stem.endswith(("_test", ".test", ".spec"))
        or stem in DEV_PATH_MARKERS
    )


def find_usage(
    root: str | Path,
    package: str,
    ecosystem: str,
    max_snippets: int = 12,
) -> list[UsageEvidence]:
    """All places the package is imported/used, capped at max_snippets."""
    root = Path(root)
    modules = import_names_for(package, ecosystem)

    if ecosystem == "npm":
        suffixes = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
        pattern_sets = [p for m in modules for p in _js_patterns(m)]
    else:
        suffixes = (".py",)
        pattern_sets = [p for m in modules for p in _python_patterns(m)]

    evidence: list[UsageEvidence] = []
    for path in _iter_source_files(root, suffixes):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        rel = str(path.relative_to(root)).replace("\\", "/")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(p.search(line) for p in pattern_sets):
                evidence.append(UsageEvidence(
                    file=rel, line=lineno, snippet=line.strip()[:200]
                ))
                if len(evidence) >= max_snippets:
                    return evidence
    return evidence
