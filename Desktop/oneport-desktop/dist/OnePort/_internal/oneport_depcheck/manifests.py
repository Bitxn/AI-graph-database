"""
Manifest and lockfile parsers. Pure functions, no network, no LLM.

Supported:
  - requirements.txt (+ requirements-dev.txt / requirements_dev.txt / dev-requirements.txt)
  - pyproject.toml (PEP 621 [project.dependencies] + optional-dependencies; Poetry)
  - poetry.lock
  - Pipfile.lock
  - package.json
  - package-lock.json (v1, v2/v3)

Every parsed Package carries the manifest path and the 1-based line number of
its declaration, so findings can be anchored to the exact line in a PR review.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from oneport_depcheck.result import Package

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

# Filenames we recognise, in discovery order.
PYTHON_REQ_FILES = (
    "requirements.txt",
    "requirements-dev.txt",
    "requirements_dev.txt",
    "dev-requirements.txt",
    "requirements-test.txt",
)

_REQ_LINE_RE = re.compile(
    r"""^\s*
    (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)          # package name
    (?:\[[^\]]*\])?                                # optional extras [sec,socks]
    \s*
    (?P<op>==|===|>=|<=|~=|!=|>|<)?\s*
    (?P<version>[0-9][^\s;,#]*)?                   # version if any
    """,
    re.VERBOSE,
)


_MANIFEST_NAMES = (*PYTHON_REQ_FILES, "pyproject.toml", "poetry.lock", "Pipfile.lock",
                   "package-lock.json", "package.json")
# Never descend into these — they hold thousands of vendored manifests that are
# not this project's declared dependencies.
_SKIP_DIRS = {
    "node_modules", ".venv", "venv", ".git", "__pycache__", ".tox", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".next", ".nuxt", "vendor", "site-packages",
    ".gradle", "target", ".idea", ".ruff_cache",
}
_MAX_MANIFEST_DEPTH = 4  # backend/, frontend/, services/api/, apps/web/src/ …


def discover_manifests(root: str | Path) -> list[Path]:
    """Find supported manifests, recursing into sub-projects.

    A polyglot/monorepo layout puts the REAL manifests one level down —
    backend/pyproject.toml, frontend/package.json — while the root holds only
    dev tooling (or nothing). The old root-only scan reported "0 packages …
    PASS" on the FastAPI full-stack template: a security gate going blind on the
    most common project shape and calling it clean. Bounded depth + a vendor-dir
    denylist keeps this from wandering into node_modules.
    """
    root = Path(root)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth > _MAX_MANIFEST_DEPTH:
            dirnames[:] = []
            continue
        names = set(filenames)
        for name in _MANIFEST_NAMES:
            if name in names:
                found.append(Path(dirpath) / name)
    return found


def parse_all(root: str | Path) -> tuple[list[Package], list[str]]:
    """Parse every discovered manifest. Returns (packages, manifest_paths).

    The lockfile-supersedes-manifest logic is applied PER DIRECTORY: a
    backend/poetry.lock must not suppress frontend/package.json, and a root
    package-lock.json must not suppress backend/package.json.
    """
    root = Path(root)
    manifests = discover_manifests(root)

    # names present in each manifest's own directory
    by_dir: dict[Path, set[str]] = {}
    for m in manifests:
        by_dir.setdefault(m.parent, set()).add(m.name)

    packages: list[Package] = []
    used: list[str] = []
    for path in manifests:
        siblings = by_dir.get(path.parent, set())
        # package.json is redundant when its lockfile sits beside it.
        if path.name == "package.json" and "package-lock.json" in siblings:
            continue
        # A resolved Poetry/Pipenv lockfile beside pyproject.toml supersedes it
        # (exact versions vs. ranges) — parse the lock, skip the source manifest.
        if path.name == "pyproject.toml" and (
            "poetry.lock" in siblings or "Pipfile.lock" in siblings
        ):
            continue
        packages.extend(parse_manifest(path, root))
        used.append(str(path.relative_to(root)).replace("\\", "/"))
    return packages, used


def parse_manifest(path: Path, root: Path) -> list[Package]:
    rel = str(path.relative_to(root)).replace("\\", "/")
    name = path.name
    if name in PYTHON_REQ_FILES:
        is_dev = name != "requirements.txt"
        return parse_requirements(path, rel, is_dev=is_dev)
    if name == "pyproject.toml":
        return parse_pyproject(path, rel)
    if name == "poetry.lock":
        return parse_poetry_lock(path, rel)
    if name == "Pipfile.lock":
        return parse_pipfile_lock(path, rel)
    if name == "package.json":
        return parse_package_json(path, rel)
    if name == "package-lock.json":
        return parse_package_lock(path, rel)
    return []


# ── Python ─────────────────────────────────────────────────────────────────────

def parse_requirements(path: Path, rel: str, is_dev: bool = False) -> list[Package]:
    packages: list[Package] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "--")):
            continue  # blank, -r includes, --index-url, etc.
        match = _REQ_LINE_RE.match(line)
        if not match or not match.group("name"):
            continue
        op = match.group("op")
        version = match.group("version")
        pinned = op in ("==", "===") and version is not None
        packages.append(Package(
            name=match.group("name"),
            version=version if version else None,
            ecosystem="PyPI",
            manifest=rel,
            line=lineno,
            is_dev=is_dev,
            version_exact=pinned,
        ))
    return packages


# Optional-dependency / Poetry groups we treat as dev (affects severity policy,
# never whether a package is scanned).
_DEV_GROUPS = {"dev", "develop", "test", "tests", "testing", "docs", "doc",
               "lint", "typing", "type", "types", "check", "ci", "build"}


def _is_zero_floor(version: str) -> bool:
    """True for a floor that means "any version at all" (`>=0`, `>=0.0`).

    saleor declares `django-mptt>=0,<1`. Taking "0" as the version and querying
    OSV with it matches EVERY vulnerability ever fixed in the package, because 0
    sorts below every fix version — a guaranteed false positive on a real
    dependency. `>=8.4.0` is informative (the project won't run below it); `>=0`
    is not information at all, so it must be treated as unpinned.
    """
    try:
        from packaging.version import Version
        return Version(version) == Version("0")
    except Exception:
        return False


def _parse_req_string(spec: str, rel: str, lineno: int, is_dev: bool) -> Package | None:
    """Parse one PEP 508 requirement (e.g. 'Werkzeug>=3.1', 'click[extra]>=8;marker')."""
    spec = spec.split(";", 1)[0].strip()  # drop environment markers
    match = _REQ_LINE_RE.match(spec)
    if not match or not match.group("name"):
        return None
    op = match.group("op")
    version = match.group("version")
    pinned = op in ("==", "===") and version is not None
    # An unpinned zero floor conveys nothing about the installed version, so it
    # goes down the existing "unpinned — could not be checked" path, which says
    # so out loud, rather than being silently checked against a fake version.
    if not pinned and version is not None and _is_zero_floor(version):
        version = None
    return Package(
        name=match.group("name"),
        version=version if version else None,  # often a range floor; not exact
        ecosystem="PyPI",
        manifest=rel,
        line=lineno,
        is_dev=is_dev,
        version_exact=pinned,
    )


def _poetry_version(spec) -> str | None:
    """Poetry dep value → floor version. Handles '^4.2', {version='^4.2', ...}."""
    if isinstance(spec, dict):
        spec = spec.get("version", "")
    if not isinstance(spec, str) or not spec:
        return None
    return spec.lstrip("^~>=<= ").split(",")[0].strip() or None


def _find_req_line(lines: list[str], needle: str) -> int:
    """Best-effort: first line mentioning the package name, to anchor findings."""
    name = re.split(r"[<>=!~;\[ ]", needle, 1)[0].strip().lower()
    for lineno, line in enumerate(lines, start=1):
        if name and name in line.lower():
            return lineno
    return 1


def parse_pyproject(path: Path, rel: str) -> list[Package]:
    """PEP 621 `[project.dependencies]` + `[project.optional-dependencies]`, and
    Poetry's `[tool.poetry.dependencies]` / `[tool.poetry.group.*.dependencies]`.

    This is how most modern Python repos (flask, django, requests) declare deps.
    Versions here are usually ranges (>=x), so the floor is recorded with
    version_exact=False — a lockfile, when present, supersedes this (see parse_all).
    """
    text = path.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    lines = text.splitlines()
    packages: list[Package] = []
    seen: set[str] = set()

    def add(pkg: Package | None) -> None:
        if pkg and pkg.name.lower() not in seen:
            seen.add(pkg.name.lower())
            packages.append(pkg)

    # PEP 621
    project = data.get("project") or {}
    for spec in project.get("dependencies") or []:
        if isinstance(spec, str):
            add(_parse_req_string(spec, rel, _find_req_line(lines, spec), is_dev=False))
    for group, specs in (project.get("optional-dependencies") or {}).items():
        dev = group.lower() in _DEV_GROUPS
        for spec in specs or []:
            if isinstance(spec, str):
                add(_parse_req_string(spec, rel, _find_req_line(lines, spec), is_dev=dev))

    # Poetry (pre-PEP621) style
    poetry = (data.get("tool") or {}).get("poetry") or {}
    for name, spec in (poetry.get("dependencies") or {}).items():
        if name.lower() == "python":
            continue
        add(Package(name=name, version=_poetry_version(spec), ecosystem="PyPI",
                    manifest=rel, line=_find_req_line(lines, name),
                    is_dev=False, version_exact=False))
    for group, gdata in (poetry.get("group") or {}).items():
        dev = group.lower() in _DEV_GROUPS or True  # a named group is dev-ish by default
        for name, spec in ((gdata or {}).get("dependencies") or {}).items():
            if name.lower() == "python":
                continue
            add(Package(name=name, version=_poetry_version(spec), ecosystem="PyPI",
                        manifest=rel, line=_find_req_line(lines, name),
                        is_dev=dev, version_exact=False))
    return packages


def parse_poetry_lock(path: Path, rel: str) -> list[Package]:
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    line_index = _name_line_index(text.splitlines(), 'name = "{}"')

    packages: list[Package] = []
    for entry in data.get("package", []):
        name = entry.get("name")
        version = entry.get("version")
        if not name or not version:
            continue
        # Poetry <1.5 wrote category = "dev"; newer lockfiles carry group info
        # only in pyproject, so is_dev stays False there (documented limitation).
        is_dev = entry.get("category") == "dev"
        packages.append(Package(
            name=name,
            version=version,
            ecosystem="PyPI",
            manifest=rel,
            line=line_index.get(name.lower(), 1),
            is_dev=is_dev,
        ))
    return packages


def parse_pipfile_lock(path: Path, rel: str) -> list[Package]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    line_index = _name_line_index(text.splitlines(), '"{}"')

    packages: list[Package] = []
    for section, is_dev in (("default", False), ("develop", True)):
        for name, meta in (data.get(section) or {}).items():
            version = (meta or {}).get("version", "")
            version = version.lstrip("=") if version else None
            packages.append(Package(
                name=name,
                version=version,
                ecosystem="PyPI",
                manifest=rel,
                line=line_index.get(name.lower(), 1),
                is_dev=is_dev,
            ))
    return packages


# ── npm ────────────────────────────────────────────────────────────────────────

_SEMVER_EXACT_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+].*)?$")


def parse_package_json(path: Path, rel: str) -> list[Package]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    line_index = _name_line_index(text.splitlines(), '"{}"')

    packages: list[Package] = []
    for section, is_dev in (("dependencies", False), ("devDependencies", True)):
        for name, spec in (data.get(section) or {}).items():
            version = spec.lstrip("^~>=<v ").split(" ")[0] if isinstance(spec, str) else ""
            exact = isinstance(spec, str) and bool(_SEMVER_EXACT_RE.match(spec))
            packages.append(Package(
                name=name,
                version=version or None,
                ecosystem="npm",
                manifest=rel,
                line=line_index.get(name.lower(), 1),
                is_dev=is_dev,
                version_exact=exact,
            ))
    return packages


def parse_package_lock(path: Path, rel: str) -> list[Package]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    line_index = _name_line_index(text.splitlines(), '"{}"')

    packages: list[Package] = []
    seen: set[tuple[str, str]] = set()

    lock_packages = data.get("packages")
    if isinstance(lock_packages, dict):  # lockfile v2/v3
        for key, info in lock_packages.items():
            if not key:  # "" is the root project itself
                continue
            name = key.rsplit("node_modules/", 1)[-1]
            version = (info or {}).get("version")
            if not name or not version or (name, version) in seen:
                continue
            seen.add((name, version))
            packages.append(Package(
                name=name,
                version=version,
                ecosystem="npm",
                manifest=rel,
                line=line_index.get(name.lower(), 1),
                is_dev=bool((info or {}).get("dev")),
            ))
        return packages

    def walk(deps: dict) -> None:  # lockfile v1
        for name, info in (deps or {}).items():
            version = (info or {}).get("version")
            if name and version and (name, version) not in seen:
                seen.add((name, version))
                packages.append(Package(
                    name=name,
                    version=version,
                    ecosystem="npm",
                    manifest=rel,
                    line=line_index.get(name.lower(), 1),
                    is_dev=bool((info or {}).get("dev")),
                ))
            walk((info or {}).get("dependencies") or {})

    walk(data.get("dependencies") or {})
    return packages


# ── Helpers ────────────────────────────────────────────────────────────────────

def _name_line_index(lines: list[str], template: str) -> dict[str, int]:
    """Map lowercase package name → first line number where `template.format(name)`
    appears. Good enough to anchor a PR comment on the right lockfile line."""
    index: dict[str, int] = {}
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        # Extract the quoted token this line declares, if any.
        match = re.match(r'^(?:name\s*=\s*)?"([^"]+)"', stripped)
        if match:
            key = match.group(1).lower()
            index.setdefault(key, lineno)
    return index
