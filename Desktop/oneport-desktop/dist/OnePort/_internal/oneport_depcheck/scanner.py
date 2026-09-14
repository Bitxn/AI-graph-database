"""
Scan orchestration: parse manifests → OSV → licenses → (optional) LLM triage
→ upgrade guidance → ranked result. Detection never touches the model.
"""

from __future__ import annotations

import time
from pathlib import Path

from oneport_depcheck.cache import NullCache, ScanCache
from oneport_depcheck.config import Config
from oneport_depcheck.guidelines import load_guidelines
from oneport_depcheck.licenses import check_licenses
from oneport_depcheck.manifests import parse_all
from oneport_depcheck.osv import scan_packages
from oneport_depcheck.result import ScanResult
from oneport_depcheck.triage import triage_findings
from oneport_depcheck.upgrade import add_upgrade_guidance


def run_scan(
    target: str,
    config: Config,
    use_llm: bool = True,
    use_cache: bool = True,
    check_license: bool = True,
) -> ScanResult:
    started = time.monotonic()
    root = str(Path(target).resolve())

    cache = ScanCache(ttl=config.cache_ttl) if use_cache else NullCache()

    packages, manifests = parse_all(root)
    findings, skipped_unpinned = scan_packages(packages, cache)

    license_issues = check_licenses(packages, cache) if check_license else []

    total_tokens = 0
    if use_llm and findings:
        guidelines = load_guidelines(root, config.guidelines_path)
        total_tokens = triage_findings(findings, root, config, guidelines)

    add_upgrade_guidance(findings, root)
    findings.sort(key=lambda f: f.sort_key())

    return ScanResult(
        findings=findings,
        license_issues=license_issues,
        packages_scanned=len(packages),
        manifests=manifests,
        skipped_unpinned=skipped_unpinned,
        target=target,
        model=config.model,
        triaged=use_llm,
        total_tokens=total_tokens,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
