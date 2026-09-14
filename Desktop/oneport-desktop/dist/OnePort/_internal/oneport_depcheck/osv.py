"""
OSV.dev client — the deterministic vulnerability source of truth.

POST https://api.osv.dev/v1/query with {package, ecosystem, version} returns
only the vulnerabilities that affect that exact version. Free, public, no key.
The model is NEVER consulted about whether a package is vulnerable; if OSV
doesn't report it, we don't report it.
"""

from __future__ import annotations

import concurrent.futures
import re
import threading

import httpx

from oneport_depcheck.cache import ScanCache
from oneport_depcheck.cvss import base_score, score_to_severity
from oneport_depcheck.exceptions import OSVError
from oneport_depcheck.result import Finding, Package, Severity

OSV_QUERY_URL = "https://api.osv.dev/v1/query"

# Concurrent OSV lookups. Enough to make a 300-package app finish in seconds,
# low enough to stay a polite client of a free public API.
_OSV_MAX_WORKERS = 16


def query_osv(
    package: Package,
    cache: ScanCache,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Raw OSV vulns affecting this exact package version. Cached 24h."""
    key = f"osv:{package.ecosystem}:{package.name}:{package.version}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    payload = {
        "package": {"name": package.name, "ecosystem": package.ecosystem},
        "version": package.version,
    }
    try:
        if client is not None:
            resp = client.post(OSV_QUERY_URL, json=payload, timeout=15)
        else:
            resp = httpx.post(OSV_QUERY_URL, json=payload, timeout=15)
    except httpx.HTTPError as exc:
        raise OSVError(f"OSV.dev request failed for {package.name}: {exc}") from exc

    if not resp.is_success:
        raise OSVError(
            f"OSV.dev error {resp.status_code} for {package.name}: {resp.text[:200]}"
        )

    vulns = resp.json().get("vulns", []) or []
    cache.set(key, vulns)
    return vulns


def _summary_of(vuln: dict, package: str = "") -> str:
    """A one-line title for an advisory. Never empty, never boilerplate.

    Not every OSV record has `summary` — some (e.g. CVE-2024-26130) carry only
    `details` prose, and "" rendered the finding as "(no message)": a MUST-FIX
    blocker with no description.

    Naively taking the first sentence of `details` is no better: GHSA advisories
    conventionally open with a package blurb ("cryptography is a package designed
    to expose cryptographic primitives...") and only describe the actual flaw in
    the NEXT sentence. That blurb is worse than nothing — it looks like a
    description while saying nothing about the vulnerability. So we skip it.

    Order: summary -> first non-blurb sentence of details -> advisory id.
    """
    summary = (vuln.get("summary") or "").strip()
    if summary:
        return summary

    details = (vuln.get("details") or "").strip()
    if details:
        for line in details.splitlines():
            line = line.strip()
            # Skip blanks and markdown furniture (headings/quotes/bullets/tables).
            if not line or line.startswith(("#", ">", "-", "*", "|")):
                continue
            for sentence in re.split(r"(?<=[.!?])\s+", line):
                sentence = sentence.strip().rstrip(".")
                if len(sentence) < 15 or _is_package_blurb(sentence, package):
                    continue
                return (sentence[:157] + "...") if len(sentence) > 160 else sentence

    return vuln.get("id", "") or "Unnamed advisory"


def _is_package_blurb(sentence: str, package: str) -> bool:
    """True for the "<package> is a library that..." opener GHSA prepends."""
    if not package:
        return False
    low = sentence.lower().lstrip("`'\"")
    if not low.startswith(package.lower()):
        return False
    return any(p in low for p in (" is a ", " is an ", " is the ", " provides ",
                                  " allows developers", " is a package"))


def severity_of(vuln: dict) -> tuple[Severity, float | None]:
    """Deterministic severity: computed CVSS 3.x base score when a vector is
    present, else the advisory's own database_specific.severity label."""
    best_score: float | None = None
    for entry in vuln.get("severity", []) or []:
        if entry.get("type") in ("CVSS_V3",):
            score = base_score(entry.get("score", ""))
            if score is not None and (best_score is None or score > best_score):
                best_score = score

    if best_score is not None:
        return Severity(score_to_severity(best_score)), best_score

    label = ((vuln.get("database_specific") or {}).get("severity") or "").upper()
    if label == "MODERATE":
        label = "MEDIUM"
    if label in Severity.__members__:
        return Severity(label), None
    return Severity.UNKNOWN, None


def fixed_version_of(vuln: dict, package: Package) -> str | None:
    """The lowest release that fixes this vuln for this package, per OSV ranges."""
    fixes: list[str] = []
    for affected in vuln.get("affected", []) or []:
        pkg = affected.get("package") or {}
        if (pkg.get("ecosystem") or "").lower() != package.ecosystem.lower():
            continue
        if (pkg.get("name") or "").lower() != package.name.lower():
            continue
        for rng in affected.get("ranges", []) or []:
            for event in rng.get("events", []) or []:
                if "fixed" in event:
                    fixes.append(event["fixed"])
    if not fixes:
        return None
    return _max_version(fixes)


def _max_version(versions: list[str]) -> str:
    from packaging.version import InvalidVersion, Version

    def key(v: str):
        try:
            return (1, Version(v))
        except InvalidVersion:
            return (0, v)

    return max(versions, key=key)


def scan_packages(
    packages: list[Package],
    cache: ScanCache,
    client: httpx.Client | None = None,
    on_progress=None,
) -> tuple[list[Finding], list[str]]:
    """Query OSV for every pinned package. Returns (findings, skipped_unpinned).

    Unpinned packages (no version to query) are reported back to the caller so
    the output can say so honestly instead of silently claiming "clean".
    """
    findings: list[Finding] = []
    skipped: list[str] = []

    queryable: list[Package] = []
    for package in packages:
        if not package.version:
            skipped.append(f"{package.name} ({package.manifest}:{package.line})")
            continue
        queryable.append(package)

    # OSV is one network round-trip per package, and real applications have
    # hundreds of dependencies (saleor: 296) — serially that blows past any sane
    # gate timeout. Libraries (8-20 deps) never surfaced this. Fan the I/O out;
    # each package is a distinct cache key (own file), and httpx.Client is
    # thread-safe, so there is no shared-state hazard. ex.map preserves input
    # order, keeping findings deterministic.
    progress_lock = threading.Lock()

    def _vulns_for(package: Package) -> list[dict]:
        found = query_osv(package, cache, client=client)
        if on_progress:
            with progress_lock:
                on_progress(package, len(found))
        return found

    if queryable:
        workers = min(_OSV_MAX_WORKERS, len(queryable))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            vulns_per_package = list(pool.map(_vulns_for, queryable))
    else:
        vulns_per_package = []

    for package, vulns in zip(queryable, vulns_per_package):
        package_findings: list[Finding] = []
        for vuln in vulns:
            severity, cvss = severity_of(vuln)
            package_findings.append(Finding(
                package=package.name,
                version=package.version,
                ecosystem=package.ecosystem,
                manifest=package.manifest,
                manifest_line=package.line,
                is_dev=package.is_dev,
                version_exact=package.version_exact,
                vuln_id=vuln.get("id", ""),
                aliases=vuln.get("aliases", []) or [],
                summary=_summary_of(vuln, package.name),
                details=(vuln.get("details", "") or "")[:4000],
                severity=severity,
                cvss_score=cvss,
                fixed_version=fixed_version_of(vuln, package),
                references=[
                    r.get("url", "") for r in (vuln.get("references") or [])[:5]
                ],
            ))
        findings.extend(_dedup_by_cve(package_findings))

    return findings, skipped


def _dedup_by_cve(findings: list[Finding]) -> list[Finding]:
    """OSV often returns the GHSA and PYSEC/etc. records for the SAME CVE as
    separate vulns. Merge them: one finding per CVE, best data wins."""
    by_cve: dict[str, Finding] = {}
    out: list[Finding] = []
    for f in findings:
        key = f.cve  # first CVE alias, or the OSV id when there's no CVE
        existing = by_cve.get(key)
        if existing is None:
            by_cve[key] = f
            out.append(f)
            continue
        # Merge into the record we're keeping.
        for alias in [f.vuln_id, *f.aliases]:
            if alias and alias != existing.vuln_id and alias not in existing.aliases:
                existing.aliases.append(alias)
        for url in f.references:
            if url not in existing.references:
                existing.references.append(url)
        if f.severity.rank > existing.severity.rank:
            existing.severity = f.severity
        if f.cvss_score and not existing.cvss_score:
            existing.cvss_score = f.cvss_score
        if f.summary and not existing.summary:
            existing.summary = f.summary
        if f.details and not existing.details:
            existing.details = f.details
        if f.fixed_version:
            if not existing.fixed_version:
                existing.fixed_version = f.fixed_version
            else:
                existing.fixed_version = _max_version(
                    [existing.fixed_version, f.fixed_version]
                )
    return out
