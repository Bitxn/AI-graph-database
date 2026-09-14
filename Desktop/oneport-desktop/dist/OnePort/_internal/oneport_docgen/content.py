# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Turn a RepoIndex into a structured Document (a Technical Design Document).

Two kinds of section:
  * Deterministic (Module Breakdown, Technology Stack, Appendix) — built straight
    from the index so they're always accurate and carry provenance (real paths).
  * Narrative (Executive Summary, System Overview, Architecture, Data Flow,
    Setup & Deployment, Risks & Considerations) — written by the model in ONE
    batched JSON call to stay well under free-tier rate limits, with a heuristic
    fallback so the tool still produces a real document with no key at all.
"""
from __future__ import annotations

import json
import re

from oneport_docgen.document import Block, Document, DocMeta, Section

# Narrative sections we ask the model to write, in order.
_NARRATIVE = [
    ("Executive Summary",
     "A 3-4 sentence summary for a non-technical stakeholder: what the system is, "
     "who it's for, and its overall shape."),
    ("System Overview",
     "What the system does and its major responsibilities, in plain prose (one short paragraph)."),
    ("Architecture",
     "How the system is structured: the main layers/modules and how they interact. "
     "Reference the real module names."),
    ("Data Flow",
     "How a typical request or unit of work moves through the system, entry point to output."),
    ("Setup & Deployment",
     "How an engineer would install, configure and run the project, based on the stack and entry points."),
    ("Risks & Considerations",
     "Notable technical risks, coupling, or areas needing attention, inferred from the structure."),
]


async def build_document(index, meta: DocMeta, llm=None, scope_module: str | None = None) -> Document:
    """Assemble the full Technical Design Document from `index`."""
    modules = _scoped_modules(index, scope_module)
    modules = sorted(modules, key=lambda m: -m.loc)

    narrative = await _narrative_sections(index, modules, llm) if llm \
        else _heuristic_narrative(index, modules)

    doc = Document(
        meta=meta,
        summary=(index.summary or "").strip(),
        at_a_glance=_at_a_glance(index, modules),
    )

    # Interleave: narrative intro sections, then the concrete deterministic ones.
    doc.sections.append(_section("Executive Summary", narrative))
    doc.sections.append(_section("System Overview", narrative))
    doc.sections.append(_section("Architecture", narrative))
    doc.sections.append(_module_breakdown(modules))
    doc.sections.append(_tech_stack(index))
    doc.sections.append(_section("Data Flow", narrative))
    doc.sections.append(_section("Setup & Deployment", narrative))
    doc.sections.append(_section("Risks & Considerations", narrative))
    doc.sections.append(_appendix(modules))

    doc.number_sections()
    return doc


# --------------------------------------------------------------------------- #
# Deterministic sections                                                        #
# --------------------------------------------------------------------------- #
def _at_a_glance(index, modules) -> dict[str, str]:
    langs = sorted(index.languages, key=lambda k: -index.languages[k])
    return {
        "Project": index.name,
        "Files": f"{index.file_count:,}",
        "Lines of code": f"{index.total_loc:,}",
        "Modules": str(len(modules)),
        "Primary language": langs[0] if langs else "—",
        "Entry points": ", ".join(index.entry_points[:5]) or "—",
        "Analysis brain": index.model_used,
    }


def _module_breakdown(modules) -> Section:
    rows = [["Module", "Files", "Responsibility"]]
    for m in modules[:40]:
        rows.append([m.name, str(len(m.files)), (m.summary or "—")[:200]])
    blocks = [
        Block.para("The system is organised into the following modules. Responsibilities "
                   "are derived directly from the source tree, so this table doubles as a "
                   "provenance map."),
        Block.table(rows),
    ]
    # Per-module dependency notes, where present.
    deps = [f"{m.name} depends on {', '.join(m.depends_on)}" for m in modules if m.depends_on]
    if deps:
        blocks.append(Block.sub("Inter-module dependencies"))
        blocks.append(Block.bullets(deps[:25]))
    return Section("Module Breakdown", blocks=blocks)


def _tech_stack(index) -> Section:
    rows = [["Language", "Files"]]
    for k, v in sorted(index.languages.items(), key=lambda x: -x[1]):
        rows.append([k, str(v)])
    return Section("Technology Stack", blocks=[
        Block.para("Languages detected across the codebase, by file count:"),
        Block.table(rows),
    ])


def _appendix(modules) -> Section:
    blocks = [Block.para("A condensed inventory of source files by module, for reference "
                         "and traceability.")]
    for m in modules[:25]:
        blocks.append(Block.sub(m.name))
        items = []
        for f in m.files[:15]:
            items.append(f"{f.path}" + (f" — {f.summary}" if f.summary else ""))
        blocks.append(Block.bullets(items or ["(no files)"]))
    return Section("Appendix — File Inventory", blocks=blocks)


# --------------------------------------------------------------------------- #
# Narrative sections (LLM, one batched call)                                    #
# --------------------------------------------------------------------------- #
async def _narrative_sections(index, modules, llm) -> dict[str, str]:
    from oneport_debug_core.llm.base import LLMCompletionOptions

    mods = "\n".join(f"- {m.name}: {m.summary}" for m in modules[:35])
    wants = "\n".join(f'  - "{name}": {brief}' for name, brief in _NARRATIVE)
    langs = ", ".join(sorted(index.languages, key=lambda k: -index.languages[k]))
    prompt = (
        "You are a senior engineer writing a formal Technical Design Document. "
        "Write each requested section as clear, professional prose (no marketing tone). "
        "Return ONLY a JSON object mapping each section name to its text. Plain text per "
        "value (short paragraphs separated by blank lines; use '- ' for bullet lines). "
        "No markdown headings, no code fences.\n\n"
        f"Project: {index.name}\n"
        f"Summary: {index.summary}\n"
        f"Languages: {langs}\n"
        f"Entry points: {', '.join(index.entry_points) or 'unknown'}\n\n"
        f"Modules:\n{mods}\n\n"
        f"Sections to write:\n{wants}\n"
    )
    try:
        raw = await llm.complete(prompt, LLMCompletionOptions(temperature=0.3, max_tokens=4000))
        parsed = _parse_sections(raw)
    except Exception:
        parsed = {}
    heuristic = _heuristic_narrative(index, modules)
    # Fill any missing/empty section from the heuristic baseline.
    return {name: (parsed.get(name) or heuristic[name]) for name, _ in _NARRATIVE}


def _parse_sections(raw: str) -> dict[str, str]:
    s = re.sub(r"^\s*```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    s = re.sub(r"\s*```\s*$", "", s)
    first, last = s.find("{"), s.rfind("}")
    if first == -1 or last <= first:
        return {}
    try:
        obj = json.loads(s[first:last + 1])
    except Exception:
        return {}
    return {k: str(v).strip() for k, v in obj.items() if isinstance(v, str) and v.strip()}


def _heuristic_narrative(index, modules) -> dict[str, str]:
    langs = sorted(index.languages, key=lambda k: -index.languages[k])
    top = ", ".join(m.name for m in modules[:6]) or "its modules"
    entries = ", ".join(index.entry_points[:5]) or "the primary scripts"
    return {
        "Executive Summary":
            f"{index.name} is a {', '.join(langs[:3])} project comprising {index.file_count} "
            f"files and roughly {index.total_loc:,} lines of code, organised into {len(modules)} "
            f"modules by responsibility. This document describes its structure, key components "
            f"and how to run it, for engineers and reviewers onboarding to the codebase.",
        "System Overview":
            index.summary or f"{index.name} is organised into {len(modules)} cooperating modules.",
        "Architecture":
            f"The codebase is organised into {len(modules)} modules ({top}). Each module groups "
            f"related files by responsibility; dependencies between modules define the flow of "
            f"control across the system.",
        "Data Flow":
            f"Execution typically begins at {entries} and flows inward through the module layers, "
            f"with each module handling its own concern before returning results outward.",
        "Setup & Deployment":
            f"Install the project's dependencies for its stack ({', '.join(langs[:3])}), then run "
            f"one of the entry points ({entries}). See the repository README for exact commands.",
        "Risks & Considerations":
            "Areas warranting review include modules with many inter-dependencies (higher change "
            "cost) and any files lacking documentation. A follow-up compliance/security pass is "
            "recommended for regulated deployments.",
    }


# --------------------------------------------------------------------------- #
# Helpers                                                                        #
# --------------------------------------------------------------------------- #
def _section(name: str, narrative: dict[str, str]) -> Section:
    """Build a Section from a narrative text blob, splitting bullets from paragraphs."""
    text = narrative.get(name, "").strip()
    blocks: list[Block] = []
    bucket: list[str] = []

    def flush_bullets():
        nonlocal bucket
        if bucket:
            blocks.append(Block.bullets(bucket))
            bucket = []

    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
        if lines and all(ln.startswith(("- ", "* ")) for ln in lines):
            bucket.extend(ln[2:].strip() for ln in lines)
        else:
            flush_bullets()
            blocks.append(Block.para(para))
    flush_bullets()
    if not blocks:
        blocks.append(Block.para("—"))
    return Section(name, blocks=blocks)


def _scoped_modules(index, scope_module: str | None):
    if not scope_module:
        return list(index.modules)
    want = scope_module.strip().lower()
    hit = [m for m in index.modules if m.name.lower() == want]
    if not hit:
        hit = [m for m in index.modules if want in m.name.lower()]
    return hit or list(index.modules)
