"""Terminal formatter — plain text, pipe-safe, ranked fix-first."""

from __future__ import annotations

from oneport_depcheck.result import Reachability, ScanResult

_REACH_BADGE = {
    Reachability.REACHABLE: "REACHABLE        ",
    Reachability.LIKELY_UNREACHABLE: "LIKELY-UNREACHABLE",
    Reachability.DEV_ONLY: "DEV-ONLY         ",
    Reachability.UNTRIAGED: "UNTRIAGED        ",
}


def format_inline(result: ScanResult) -> str:
    lines: list[str] = []
    lines.append("oneport-depcheck")
    lines.append(
        f"Scanned {result.packages_scanned} package(s) from: "
        + (", ".join(result.manifests) or "(no manifests found)")
    )
    if result.skipped_unpinned:
        lines.append(
            f"Skipped {len(result.skipped_unpinned)} unpinned requirement(s) "
            f"(no exact version to check): " + ", ".join(result.skipped_unpinned)
        )
    lines.append("")

    if not result.findings and not result.license_issues:
        if result.packages_scanned == 0:
            # 0 packages is NOT a clean bill of health — nothing was checked.
            # Saying "PASS - no vulnerabilities" here is a false green (the
            # FastAPI template's real deps live in backend/ & frontend/).
            lines.append(
                "NO PACKAGES CHECKED — 0 dependencies parsed"
                + (f" from {', '.join(result.manifests)}" if result.manifests
                   else " (no manifest found)")
                + ". This is NOT a pass; nothing was scanned."
            )
        else:
            lines.append("PASS - no known vulnerabilities, no license issues.")
        return "\n".join(lines)

    if result.findings:
        counts = result.counts_by_severity()
        reach = result.counts_by_reachability()
        summary = ", ".join(f"{v} {k}" for k, v in counts.items())
        lines.append(f"{len(result.findings)} vulnerability finding(s): {summary}")
        if result.triaged:
            lines.append(
                "Triage: "
                + ", ".join(f"{v} {k}" for k, v in reach.items())
                + f"  (model: {result.model})"
            )
        else:
            lines.append("Triage: skipped (--no-llm) - findings are unranked by reachability.")
        lines.append("")

        for i, f in enumerate(result.findings, start=1):
            waived = " [WAIVED]" if f.waived else ""
            lines.append(
                f"{i}. [{f.severity.value}] [{_REACH_BADGE[f.reachability].strip()}] "
                f"{f.spec}  {f.cve}{waived}"
            )
            lines.append(f"   {f.manifest}:{f.manifest_line}")
            if f.waived:
                reason = f" - {f.waiver_reason}" if f.waiver_reason else ""
                lines.append(f"   waived: does not block CI{reason}")
            if f.summary:
                lines.append(f"   {f.summary}")
            if f.reachability_reason:
                lines.append(f"   Why: {f.reachability_reason}")
            for e in f.evidence[:3]:
                lines.append(f"   Evidence: {e.file}:{e.line}  {e.snippet}")
            if f.upgrade_target:
                lines.append(f"   Fix: upgrade to {f.upgrade_target} - {f.break_risk}")
                if f.fix_suggestion:
                    lines.append(f"        {f.fix_suggestion}")
            elif f.break_risk:
                lines.append(f"   Fix: {f.break_risk}")
            lines.append("")

    if result.license_issues:
        lines.append(f"{len(result.license_issues)} license issue(s):")
        for li in result.license_issues:
            lines.append(
                f"- [{li.risk}] {li.package} {li.version or ''} - {li.license} "
                f"({li.manifest}:{li.manifest_line}) {li.detail}"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
