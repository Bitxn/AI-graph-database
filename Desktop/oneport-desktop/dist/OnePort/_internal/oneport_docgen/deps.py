# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Dependency scanner — the engine behind the SBOM doctype.

Reads declared dependencies straight from a repo's manifest files across the
common ecosystems (PyPI, npm, Go, crates.io, RubyGems, Composer, Maven, Gradle).
Everything is deterministic and offline: no network calls, no guessing. For
Python we additionally cross-reference the *installed* package metadata (via
importlib.metadata) to fill in resolved versions and licenses, which is what
makes the Python SBOM genuinely useful without hitting an index.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Component:
    name: str
    ecosystem: str                 # PyPI | npm | Go | crates.io | RubyGems | Composer | Maven
    spec: str = ""                 # declared constraint, e.g. ">=2.9.0" or "^1.4.0"
    version: str = ""              # resolved/installed version, if known
    scope: str = "runtime"         # runtime | dev
    license: str = ""
    source: str = ""               # manifest file it was declared in

    @property
    def pinned(self) -> bool:
        return _pinned(self.spec, self.ecosystem)

    @property
    def flags(self) -> list[str]:
        out = []
        if not self.pinned:
            out.append("unpinned")
        if is_copyleft(self.license):
            out.append("copyleft")
        if not (self.license or "").strip():
            out.append("license unknown")
        return out


@dataclass
class Scan:
    components: list[Component] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)   # repo-relative paths scanned
    ecosystems: list[str] = field(default_factory=list)
    project_license: str = ""


# --------------------------------------------------------------------------- #
# Public entry point                                                            #
# --------------------------------------------------------------------------- #
def scan_dependencies(root: Path) -> Scan:
    root = Path(root).resolve()
    comps: list[Component] = []
    manifests: list[str] = []

    for parser, names in _PARSERS:
        for name in names:
            for path in _find(root, name):
                try:
                    found = parser(path)
                except Exception:
                    found = []
                if found:
                    rel = path.relative_to(root).as_posix()
                    for c in found:
                        c.source = rel
                    comps.extend(found)
                    manifests.append(rel)

    comps = _dedupe(comps)
    _enrich_python(comps)

    ecosystems = sorted({c.ecosystem for c in comps})
    return Scan(
        components=comps,
        manifests=sorted(set(manifests)),
        ecosystems=ecosystems,
        project_license=detect_project_license(root),
    )


# --------------------------------------------------------------------------- #
# Manifest discovery                                                            #
# --------------------------------------------------------------------------- #
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "dist", "build",
              "target", "vendor", "__pycache__", ".tox", "site-packages"}


def _find(root: Path, filename: str, max_depth: int = 3) -> list[Path]:
    """Find a manifest by name up to `max_depth` dirs deep (root + a few nested)."""
    hits: list[Path] = []
    root_str = str(root)
    for p in root.rglob(filename):
        rel_parts = p.relative_to(root).parts
        if any(part in _SKIP_DIRS for part in rel_parts[:-1]):
            continue
        if len(rel_parts) - 1 > max_depth:
            continue
        hits.append(p)
    return hits


# --------------------------------------------------------------------------- #
# Python                                                                        #
# --------------------------------------------------------------------------- #
_PEP508 = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?\s*(.*)$")


def _parse_pep508(line: str) -> tuple[str, str] | None:
    line = line.split("#", 1)[0].split(";", 1)[0].strip()
    if not line or line.startswith("-"):
        return None
    if re.match(r"^(https?://|git\+|file:)", line):
        return None
    m = _PEP508.match(line)
    if not m:
        return None
    return m.group(1), (m.group(3) or "").strip()


def _parse_requirements(path: Path) -> list[Component]:
    scope = "dev" if re.search(r"dev|test", path.name, re.I) else "runtime"
    out = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = _parse_pep508(raw)
        if parsed:
            out.append(Component(parsed[0], "PyPI", spec=parsed[1], scope=scope))
    return out


def _parse_pyproject(path: Path) -> list[Component]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: list[Component] = []
    proj = data.get("project", {})
    for dep in proj.get("dependencies", []) or []:
        p = _parse_pep508(dep)
        if p:
            out.append(Component(p[0], "PyPI", spec=p[1], scope="runtime"))
    for _grp, deps in (proj.get("optional-dependencies", {}) or {}).items():
        for dep in deps or []:
            p = _parse_pep508(dep)
            if p:
                out.append(Component(p[0], "PyPI", spec=p[1], scope="dev"))
    # Poetry
    poetry = data.get("tool", {}).get("poetry", {})
    for scope, key in (("runtime", "dependencies"), ("dev", "dev-dependencies")):
        for name, val in (poetry.get(key, {}) or {}).items():
            if name.lower() == "python":
                continue
            spec = val if isinstance(val, str) else (val.get("version", "") if isinstance(val, dict) else "")
            out.append(Component(name, "PyPI", spec=spec, scope=scope))
    for _g, grp in (poetry.get("group", {}) or {}).items():
        for name, val in (grp.get("dependencies", {}) or {}).items():
            if name.lower() == "python":
                continue
            spec = val if isinstance(val, str) else (val.get("version", "") if isinstance(val, dict) else "")
            out.append(Component(name, "PyPI", spec=spec, scope="dev"))
    return out


def _parse_pipfile(path: Path) -> list[Component]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out = []
    for scope, key in (("runtime", "packages"), ("dev", "dev-packages")):
        for name, val in (data.get(key, {}) or {}).items():
            spec = val if isinstance(val, str) and val != "*" else (
                val.get("version", "") if isinstance(val, dict) else "")
            out.append(Component(name, "PyPI", spec="" if spec == "*" else spec, scope=scope))
    return out


# --------------------------------------------------------------------------- #
# npm / JavaScript                                                              #
# --------------------------------------------------------------------------- #
def _parse_package_json(path: Path) -> list[Component]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for key, scope in (("dependencies", "runtime"), ("devDependencies", "dev"),
                       ("optionalDependencies", "runtime"), ("peerDependencies", "runtime")):
        for name, spec in (data.get(key, {}) or {}).items():
            out.append(Component(name, "npm", spec=str(spec), scope=scope))
    return out


# --------------------------------------------------------------------------- #
# Go                                                                            #
# --------------------------------------------------------------------------- #
def _parse_go_mod(path: Path) -> list[Component]:
    text = path.read_text(encoding="utf-8", errors="replace")
    out = []
    # require ( ... ) blocks and single-line requires
    block = re.findall(r"require\s*\((.*?)\)", text, re.DOTALL)
    lines = []
    for b in block:
        lines += b.splitlines()
    lines += re.findall(r"^\s*require\s+(\S+\s+\S+.*)$", text, re.M)
    for ln in lines:
        ln = ln.split("//")[0].strip()
        m = re.match(r"^(\S+)\s+(v\S+)", ln)
        if m:
            out.append(Component(m.group(1), "Go", spec=m.group(2), version=m.group(2).lstrip("v")))
    return out


# --------------------------------------------------------------------------- #
# Rust                                                                          #
# --------------------------------------------------------------------------- #
def _parse_cargo(path: Path) -> list[Component]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out = []
    for key, scope in (("dependencies", "runtime"), ("dev-dependencies", "dev"),
                       ("build-dependencies", "dev")):
        for name, val in (data.get(key, {}) or {}).items():
            spec = val if isinstance(val, str) else (val.get("version", "") if isinstance(val, dict) else "")
            out.append(Component(name, "crates.io", spec=spec, scope=scope))
    return out


# --------------------------------------------------------------------------- #
# Ruby / PHP / Java                                                             #
# --------------------------------------------------------------------------- #
def _parse_gemfile(path: Path) -> list[Component]:
    out = []
    for m in re.finditer(r"""gem\s+['"]([^'"]+)['"]\s*(?:,\s*['"]([^'"]+)['"])?""",
                         path.read_text(encoding="utf-8", errors="replace")):
        out.append(Component(m.group(1), "RubyGems", spec=m.group(2) or ""))
    return out


def _parse_composer(path: Path) -> list[Component]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for key, scope in (("require", "runtime"), ("require-dev", "dev")):
        for name, spec in (data.get(key, {}) or {}).items():
            if name.lower() == "php" or name.startswith("ext-"):
                continue
            out.append(Component(name, "Composer", spec=str(spec), scope=scope))
    return out


def _parse_pom(path: Path) -> list[Component]:
    text = path.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in re.finditer(r"<dependency>(.*?)</dependency>", text, re.DOTALL):
        blk = m.group(1)
        gid = re.search(r"<groupId>(.*?)</groupId>", blk)
        aid = re.search(r"<artifactId>(.*?)</artifactId>", blk)
        ver = re.search(r"<version>(.*?)</version>", blk)
        if aid:
            name = f"{gid.group(1)}:{aid.group(1)}" if gid else aid.group(1)
            out.append(Component(name.strip(), "Maven", spec=(ver.group(1).strip() if ver else "")))
    return out


def _parse_gradle(path: Path) -> list[Component]:
    out = []
    for m in re.finditer(r"""(?:implementation|api|compile|testImplementation|runtimeOnly)\s*[('"]+([\w.\-]+:[\w.\-]+:[\w.\-]+)""",
                         path.read_text(encoding="utf-8", errors="replace")):
        parts = m.group(1).split(":")
        name = ":".join(parts[:2])
        ver = parts[2] if len(parts) > 2 else ""
        out.append(Component(name, "Maven", spec=ver))
    return out


_PARSERS = [
    (_parse_requirements, ["requirements.txt", "requirements-dev.txt", "requirements_test.txt", "requirements-test.txt"]),
    (_parse_pyproject, ["pyproject.toml"]),
    (_parse_pipfile, ["Pipfile"]),
    (_parse_package_json, ["package.json"]),
    (_parse_go_mod, ["go.mod"]),
    (_parse_cargo, ["Cargo.toml"]),
    (_parse_gemfile, ["Gemfile"]),
    (_parse_composer, ["composer.json"]),
    (_parse_pom, ["pom.xml"]),
    (_parse_gradle, ["build.gradle", "build.gradle.kts"]),
]


# --------------------------------------------------------------------------- #
# Enrichment & helpers                                                          #
# --------------------------------------------------------------------------- #
def _dedupe(comps: list[Component]) -> list[Component]:
    seen: dict[tuple[str, str], Component] = {}
    for c in comps:
        key = (c.ecosystem, c.name.lower())
        if key not in seen:
            seen[key] = c
        else:                                    # prefer runtime scope, keep a spec
            cur = seen[key]
            if cur.scope == "dev" and c.scope == "runtime":
                cur.scope = "runtime"
            if not cur.spec and c.spec:
                cur.spec = c.spec
    return sorted(seen.values(), key=lambda c: (c.ecosystem, c.name.lower()))


def _enrich_python(comps: list[Component]) -> None:
    """Fill resolved version + license for PyPI deps from installed metadata (offline)."""
    try:
        from importlib.metadata import metadata, version, PackageNotFoundError
    except Exception:
        return
    for c in comps:
        if c.ecosystem != "PyPI":
            continue
        try:
            c.version = c.version or version(c.name)
            c.license = c.license or _license_from_metadata(metadata(c.name))
        except PackageNotFoundError:
            continue
        except Exception:
            continue


def _license_from_metadata(md) -> str:
    classifiers = md.get_all("Classifier") or []
    for cl in classifiers:
        if cl.startswith("License ::"):
            return _normalize_license(cl.split("::")[-1].strip())
    lic = (md.get("License") or "").strip()
    if lic and len(lic) < 60 and "\n" not in lic:
        return _normalize_license(lic)
    return ""


_LICENSE_MAP = [
    ("APACHE", "Apache-2.0"), ("MIT", "MIT"), ("BSD", "BSD"), ("ISC", "ISC"),
    ("AGPL", "AGPL"), ("LGPL", "LGPL"), ("GPL", "GPL"), ("MPL", "MPL"),
    ("MOZILLA", "MPL"), ("EPL", "EPL"), ("ECLIPSE", "EPL"), ("UNLICENSE", "Unlicense"),
    ("PYTHON SOFTWARE FOUNDATION", "PSF"), ("PSF", "PSF"), ("ZLIB", "Zlib"),
]


def _normalize_license(text: str) -> str:
    u = text.upper()
    for needle, short in _LICENSE_MAP:
        if needle in u:
            return short
    return text.strip()[:40]


_COPYLEFT = ("GPL", "AGPL", "LGPL", "MPL", "EPL", "CDDL", "CC-BY-SA", "OSL", "EUPL")


def is_copyleft(license: str) -> bool:
    u = (license or "").upper()
    return any(tok in u for tok in _COPYLEFT)


def _pinned(spec: str, ecosystem: str) -> bool:
    s = (spec or "").strip()
    if not s:
        return False
    if ecosystem == "PyPI":
        return "==" in s or "===" in s
    if ecosystem in ("npm", "Composer"):
        return bool(re.match(r"^\d+\.\d+", s)) and not any(x in s for x in "^~*x><|- ")
    if ecosystem in ("Go", "crates.io", "Maven", "RubyGems"):
        # exact-ish: starts with a digit (or v-digit) and has no range operators
        return bool(re.match(r"^v?\d", s)) and not any(x in s for x in "^~*><=,|")
    return False


def detect_project_license(root: Path) -> str:
    # 1) a LICENSE file's first meaningful line
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING"):
        p = root / name
        if p.exists():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if s and len(s) > 3:
                    return _normalize_license(s)
    # 2) declared in pyproject / package.json
    py = root / "pyproject.toml"
    if py.exists():
        try:
            data = tomllib.loads(py.read_text(encoding="utf-8"))
            lic = data.get("project", {}).get("license")
            if isinstance(lic, dict):
                lic = lic.get("text") or lic.get("file")
            if lic:
                return _normalize_license(str(lic))
        except Exception:
            pass
    pkg = root / "package.json"
    if pkg.exists():
        try:
            lic = json.loads(pkg.read_text(encoding="utf-8")).get("license")
            if lic:
                return _normalize_license(str(lic))
        except Exception:
            pass
    return ""
