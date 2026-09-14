"""
License detection — deterministic, registry-metadata based.

PyPI: https://pypi.org/pypi/{name}/{version}/json  → info.license / classifiers
npm:  https://registry.npmjs.org/{name}/{version}  → license

Classification targets a proprietary/commercial repo: strong copyleft
(GPL/AGPL) is BLOCKED, weak copyleft is REVIEW, permissive passes silently.
Only the package name+version travels over the wire — public data.
"""

from __future__ import annotations

import httpx

from oneport_depcheck.cache import ScanCache
from oneport_depcheck.result import LicenseIssue, Package

PYPI_URL = "https://pypi.org/pypi/{name}/{version}/json"
NPM_URL = "https://registry.npmjs.org/{name}/{version}"

RISK_DETAIL = {
    "BLOCKED": "Strong copyleft - linking obliges you to open-source your code.",
    "REVIEW": "Weak copyleft - needs legal sign-off for proprietary distribution.",
    "UNKNOWN": "No license declared by the package - verify manually.",
    "UNVERIFIED": "License lookup failed (registry unreachable or version not published) "
                  "- NOT a license problem; re-run to check.",
}

# Sentinel: the registry lookup itself failed. Distinct from "" (= the package
# genuinely declares no license). Conflating them made a flaky PyPI call or a
# 404 on an unpublished version render as "License could not be determined",
# which reads to a user as a legal risk in THEIR dependency rather than as our
# lookup failing. On a 296-package repo some lookups always fail.
#
# Text-safe on purpose: this value can reach JSON output and CI logs, and a NUL
# byte makes grep/less treat the whole stream as binary. No real license string
# looks like this.
LOOKUP_FAILED = "<oneport:lookup-failed>"


def classify_license(license_str: str) -> str:
    """BLOCKED / REVIEW / ALLOWED / UNKNOWN / UNVERIFIED for a raw license string."""
    if license_str == LOOKUP_FAILED:
        return "UNVERIFIED"
    if not license_str or license_str.strip().upper() in ("", "UNKNOWN"):
        return "UNKNOWN"
    upper = license_str.upper()
    if "AGPL" in upper or "AFFERO" in upper:
        return "BLOCKED"
    if "LGPL" in upper or "LESSER" in upper:
        return "REVIEW"
    if "GPL" in upper:  # after LGPL/AGPL checks: plain GPL
        return "BLOCKED"
    if any(t in upper for t in ("MPL", "MOZILLA", "EPL", "ECLIPSE", "CDDL",
                                "EUPL", "OSL", "SSPL", "BUSL", "BSL-1.1")):
        return "REVIEW"
    # Permissive. HPND is here because Pillow ships it and a bare "HPND" was
    # classified UNKNOWN — reporting one of the most widely installed packages in
    # Python as a license risk. A permissive license we simply don't recognise is
    # a false positive, and false positives are what get a gate switched off.
    if any(t in upper for t in ("MIT", "APACHE", "BSD", "ISC", "UNLICENSE",
                                "PSF", "PYTHON SOFTWARE", "ZLIB", "WTFPL",
                                "CC0", "PUBLIC DOMAIN", "BOOST", "BSL-1.0", "0BSD",
                                "HPND", "HISTORICAL PERMISSION",
                                "AFL", "ACADEMIC FREE", "ARTISTIC", "NCSA",
                                "POSTGRESQL", "SIL OPEN FONT", "OFL",
                                "UNIVERSAL PERMISSIVE", "UPL")):
        return "ALLOWED"
    return "UNKNOWN"


def fetch_license(
    package: Package,
    cache: ScanCache,
    client: httpx.Client | None = None,
) -> str:
    """Raw license string from the package registry, cached.

    Returns LOOKUP_FAILED (not "") when the registry couldn't be reached or the
    version isn't published — the caller must not report that as "no license".
    A failed lookup is never cached: it's transient, and caching it would pin a
    network blip into the report for every later run.
    """
    key = f"license:{package.ecosystem}:{package.name}:{package.version}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    if package.ecosystem == "PyPI":
        url = PYPI_URL.format(name=package.name, version=package.version)
    else:
        url = NPM_URL.format(name=package.name, version=package.version)

    try:
        resp = client.get(url, timeout=10) if client is not None else httpx.get(url, timeout=10)
    except httpx.HTTPError:
        return LOOKUP_FAILED
    if not resp.is_success:
        return LOOKUP_FAILED

    try:
        data = resp.json()
    except ValueError:
        return LOOKUP_FAILED
    license_str = ""
    if package.ecosystem == "PyPI":
        info = data.get("info") or {}
        license_str = (info.get("license") or "").strip()
        # Long license fields are usually full license TEXT; classifiers are cleaner.
        if not license_str or len(license_str) > 80:
            for classifier in info.get("classifiers") or []:
                if classifier.startswith("License ::"):
                    license_str = classifier.split(" :: ")[-1].strip()
                    break
    else:
        raw = data.get("license")
        if isinstance(raw, dict):
            license_str = raw.get("type", "")
        elif isinstance(raw, str):
            license_str = raw

    cache.set(key, license_str)
    return license_str


def check_licenses(
    packages: list[Package],
    cache: ScanCache,
    client: httpx.Client | None = None,
) -> list[LicenseIssue]:
    """Flag BLOCKED / REVIEW / UNKNOWN licenses. ALLOWED passes silently."""
    issues: list[LicenseIssue] = []
    for package in packages:
        if not package.version:
            continue
        license_str = fetch_license(package, cache, client=client)
        risk = classify_license(license_str)
        if risk == "ALLOWED":
            continue
        if risk == "UNVERIFIED":
            shown = "unverified"
        else:
            shown = license_str or "UNKNOWN"
        issues.append(LicenseIssue(
            package=package.name,
            version=package.version,
            ecosystem=package.ecosystem,
            manifest=package.manifest,
            manifest_line=package.line,
            license=shown,
            risk=risk,
            detail=RISK_DETAIL.get(risk, ""),
        ))
    return issues
