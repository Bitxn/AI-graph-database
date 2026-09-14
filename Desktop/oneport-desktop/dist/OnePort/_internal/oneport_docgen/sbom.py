# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
SBOM doctype — a Software Bill of Materials.

Deliberately deterministic: every row comes straight from the repo's manifests
(via deps.scan_dependencies), with NO model in the loop, so the document is fully
auditable and reproducible. That's the whole point of an SBOM — a compliance
reviewer must be able to trust it. The `llm` argument is accepted for a uniform
doctype signature but intentionally unused.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from oneport_docgen.deps import scan_dependencies, is_copyleft
from oneport_docgen.document import Block, Document, DocMeta, Section

_MAX_ROWS = 500


async def build_sbom(index, meta: DocMeta, llm=None, scope_module: str | None = None) -> Document:
    scan = scan_dependencies(Path(index.root))
    comps = scan.components

    runtime = [c for c in comps if c.scope == "runtime"]
    dev = [c for c in comps if c.scope == "dev"]
    flagged = [c for c in comps if c.flags]
    licenses = Counter(c.license or "unknown" for c in comps)
    copyleft = [c for c in comps if is_copyleft(c.license)]

    doc = Document(
        meta=meta,
        summary=(f"This Software Bill of Materials enumerates {len(comps)} dependencies "
                 f"declared across {len(scan.manifests)} manifest file(s) in {index.name}, "
                 f"spanning {', '.join(scan.ecosystems) or 'no detected ecosystems'}."),
        at_a_glance={
            "Project": index.name,
            "Project license": scan.project_license or "—",
            "Total components": str(len(comps)),
            "Runtime / Dev": f"{len(runtime)} / {len(dev)}",
            "Ecosystems": ", ".join(scan.ecosystems) or "—",
            "Distinct licenses": str(len([k for k in licenses if k != 'unknown'])),
            "Findings": str(len(flagged)),
        },
    )

    doc.sections.append(_executive(index, scan, comps, flagged, copyleft))
    doc.sections.append(_scope(scan))
    doc.sections.append(_inventory(comps))
    doc.sections.append(_license_summary(licenses, copyleft))
    doc.sections.append(_findings(flagged))
    doc.sections.append(_project_info(index, scan, runtime, dev))

    doc.number_sections()
    return doc


def _executive(index, scan, comps, flagged, copyleft) -> Section:
    blocks = [
        Block.para(
            f"This Software Bill of Materials (SBOM) provides a complete, deterministic "
            f"inventory of the third-party components that **{index.name}** depends on. "
            f"It is generated directly from the project's dependency manifests — no data is "
            f"inferred or model-generated — so it can be relied upon for license compliance, "
            f"supply-chain review and audit."),
        Block.bullets([
            f"**{len(comps)}** total components across {len(scan.ecosystems)} ecosystem(s).",
            f"**{scan.project_license or 'No declared'}** project license.",
            f"**{len(copyleft)}** component(s) under a copyleft license (review for redistribution).",
            f"**{len(flagged)}** finding(s) flagged (unpinned versions, unknown or copyleft licenses).",
        ]),
    ]
    if not comps:
        blocks.append(Block.para(
            "No dependency manifests were found in this repository. If dependencies are "
            "vendored or declared elsewhere, point the tool at the directory containing the "
            "manifest (requirements.txt, package.json, go.mod, etc.)."))
    return Section("Executive Summary", blocks=blocks)


def _scope(scan) -> Section:
    blocks = [
        Block.para(
            "The following manifest files were scanned to produce this inventory. Only "
            "declared (direct) dependencies are listed; transitive dependencies are resolved "
            "by each ecosystem's package manager and are out of scope for this document."),
    ]
    if scan.manifests:
        blocks.append(Block.bullets(scan.manifests))
    else:
        blocks.append(Block.para("No manifests were found."))
    blocks.append(Block.sub("Methodology"))
    blocks.append(Block.bullets([
        "Dependencies are parsed directly from manifest files (deterministic, offline).",
        "For Python packages, resolved versions and licenses are read from the installed "
        "environment metadata where available.",
        "A component is flagged 'unpinned' when its version constraint permits a range rather "
        "than an exact version.",
    ]))
    return Section("Scope & Methodology", blocks=blocks)


def _inventory(comps) -> Section:
    if not comps:
        return Section("Component Inventory", blocks=[Block.para("No components detected.")])
    rows = [["#", "Component", "Ecosystem", "Version", "Constraint", "Scope", "License"]]
    for i, c in enumerate(comps[:_MAX_ROWS], start=1):
        rows.append([str(i), c.name, c.ecosystem, c.version or "—",
                     c.spec or "—", c.scope, c.license or "—"])
    blocks = [
        Block.para("Complete inventory of declared components. 'Version' is the resolved "
                   "version where known; 'Constraint' is the declared version specifier."),
        Block.table(rows),
    ]
    if len(comps) > _MAX_ROWS:
        blocks.append(Block.para(f"Showing the first {_MAX_ROWS} of {len(comps)} components."))
    return Section("Component Inventory", blocks=blocks)


def _license_summary(licenses: Counter, copyleft) -> Section:
    rows = [["License", "Components"]]
    for lic, n in licenses.most_common():
        label = lic + ("  (copyleft)" if is_copyleft(lic) else "")
        rows.append([label, str(n)])
    blocks = [
        Block.para("Distribution of declared/resolved licenses across all components. "
                   "Copyleft licenses are highlighted as they carry redistribution obligations."),
        Block.table(rows),
    ]
    if copyleft:
        blocks.append(Block.sub("Copyleft components"))
        blocks.append(Block.bullets([f"{c.name} ({c.license})" for c in copyleft]))
    return Section("License Summary", blocks=blocks)


def _findings(flagged) -> Section:
    if not flagged:
        return Section("Risk Findings", blocks=[
            Block.para("No findings. All components are version-pinned and carry a known, "
                       "non-copyleft license.")])
    rows = [["Component", "Ecosystem", "Findings"]]
    for c in flagged:
        rows.append([c.name, c.ecosystem, ", ".join(c.flags)])
    return Section("Risk Findings", blocks=[
        Block.para("Components warranting review before release. 'unpinned' allows a version "
                   "range (supply-chain drift); 'copyleft' may impose source-disclosure "
                   "obligations; 'license unknown' could not be resolved and needs manual check."),
        Block.table(rows),
    ])


def _project_info(index, scan, runtime, dev) -> Section:
    return Section("Project Information", blocks=[
        Block.table([
            ["Field", "Value"],
            ["Project", index.name],
            ["Declared license", scan.project_license or "—"],
            ["Direct runtime dependencies", str(len(runtime))],
            ["Direct development dependencies", str(len(dev))],
            ["Ecosystems", ", ".join(scan.ecosystems) or "—"],
            ["Manifests scanned", str(len(scan.manifests))],
        ]),
    ])
