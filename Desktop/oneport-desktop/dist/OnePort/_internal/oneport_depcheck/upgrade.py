"""
Upgrade guidance — deterministic, computed from the version gap.

For each finding: the safe target version (highest OSV "fixed" release across
the package's findings, so one bump clears them all), a one-line what-might-
break note from the semver distance, and — where the manifest line is a plain
pin — the exact replacement line as a committable suggestion.
"""

from __future__ import annotations

import re

from packaging.version import InvalidVersion, Version

from oneport_depcheck.result import Finding


def break_risk_note(current: str, target: str) -> str:
    try:
        cur, tgt = Version(current), Version(target)
    except InvalidVersion:
        return "Version gap could not be parsed - review the changelog before upgrading."
    if tgt.major > cur.major:
        gap = tgt.major - cur.major
        return (
            f"Major upgrade ({cur.major}.x -> {tgt.major}.x"
            f"{', ' + str(gap) + ' majors' if gap > 1 else ''}) - "
            "breaking API changes are likely; read the release notes."
        )
    if tgt.minor > cur.minor:
        return (
            f"Minor upgrade ({cur.major}.{cur.minor} -> {tgt.major}.{tgt.minor}) - "
            "additive changes; low breakage risk, deprecations possible."
        )
    return "Patch upgrade - bug/security fixes only; safe to apply."


# Captures the OPERATOR rather than assuming `==`, and tolerates the quoting a
# pyproject dependency entry adds (`    "cryptography>=37.0.0",`). Matching only
# `==` meant every ranged requirement fell through to a hardcoded `pkg==target`,
# which silently rewrites a floor into a hard pin — wrong for any library, and
# scrapy declares `cryptography>=37.0.0` exactly that way.
_REQ_SPEC_RE = re.compile(
    r"^(?P<prefix>\s*['\"]?[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]*\])?\s*)"
    r"(?P<op>===|==|>=|~=|>)\s*"
    r"(?P<version>[^\s;#,'\"]+)"
    r"(?P<suffix>.*)$"
)

# Manifests whose dependency lines are hand-authored requirement specs.
_SPEC_MANIFESTS = (".txt", ".in")


def _spec_fallback(finding: Finding, target: str) -> str:
    """`pkg==target` only when the manifest actually pinned; else raise the floor."""
    return f"{finding.package}{'==' if finding.version_exact else '>='}{target}"


def fix_suggestion_for(finding: Finding, manifest_line_text: str | None) -> str:
    """Exact replacement manifest line, or a regeneration command when the
    manifest is a generated lockfile (hand-editing those is the wrong fix)."""
    target = finding.upgrade_target
    if not target:
        return ""

    manifest = finding.manifest.rsplit("/", 1)[-1]

    # requirements*.txt / *.in and pyproject.toml all carry hand-authored specs,
    # so the declared operator is preserved rather than replaced with `==`.
    # pyproject was previously unhandled entirely and fell through to "" — no fix
    # suggestion at all for the manifest most modern Python projects use.
    if manifest.endswith(_SPEC_MANIFESTS) or manifest == "pyproject.toml":
        if manifest_line_text is not None:
            match = _REQ_SPEC_RE.match(manifest_line_text)
            if match and match.group("version") == finding.version:
                return (f"{match.group('prefix')}{match.group('op')}{target}"
                        f"{match.group('suffix')}").rstrip()
        return _spec_fallback(finding, target)

    if manifest == "package.json" and manifest_line_text is not None:
        pattern = re.compile(
            rf'("{re.escape(finding.package)}"\s*:\s*")[^"]*(")'
        )
        if pattern.search(manifest_line_text):
            return pattern.sub(rf"\g<1>{target}\g<2>", manifest_line_text).rstrip()

    if manifest == "poetry.lock":
        return f"poetry update {finding.package}"
    if manifest == "Pipfile.lock":
        return f"pipenv update {finding.package}"
    if manifest == "package-lock.json":
        return f"npm install {finding.package}@{target}"
    return ""


def add_upgrade_guidance(findings: list[Finding], project_root: str) -> None:
    """Fill upgrade_target / break_risk / fix_suggestion on every finding."""
    from pathlib import Path

    # One target per package: the highest fix version across all its findings.
    best_target: dict[str, str] = {}
    for f in findings:
        if not f.fixed_version:
            continue
        current = best_target.get(f.package)
        if current is None:
            best_target[f.package] = f.fixed_version
        else:
            try:
                if Version(f.fixed_version) > Version(current):
                    best_target[f.package] = f.fixed_version
            except InvalidVersion:
                pass

    line_cache: dict[str, list[str]] = {}
    for f in findings:
        target = best_target.get(f.package)
        if not target:
            f.break_risk = (
                "No fixed release published yet - consider removing or replacing "
                f"'{f.package}'."
            )
            continue
        f.upgrade_target = target
        f.break_risk = break_risk_note(f.version, target)

        if f.manifest not in line_cache:
            path = Path(project_root) / f.manifest
            try:
                line_cache[f.manifest] = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                line_cache[f.manifest] = []
        lines = line_cache[f.manifest]
        line_text = lines[f.manifest_line - 1] if 0 < f.manifest_line <= len(lines) else None
        f.fix_suggestion = fix_suggestion_for(f, line_text)
