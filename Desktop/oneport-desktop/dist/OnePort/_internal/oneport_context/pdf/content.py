# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""Build the handbook content: an at-a-glance meta dict + a Markdown body."""
from __future__ import annotations


async def build_doc(index, llm=None) -> tuple[dict, str]:
    """Return (meta, markdown). Markdown is written by Gemini in ONE call, or heuristic."""
    modules = sorted(index.modules, key=lambda m: -m.loc)
    meta = {
        "tech_stack": sorted(index.languages, key=lambda k: -index.languages[k]),
        "entry_points": index.entry_points,
        "architecture": f"Modular — {len(modules)} modules across {index.file_count} files",
        "modules": [(m.name, m.summary) for m in modules],
    }
    markdown = await _markdown(index, modules, llm) if llm else _heuristic_markdown(index, modules)
    return meta, markdown


async def _markdown(index, modules, llm) -> str:
    from oneport_debug_core.llm.base import LLMCompletionOptions
    mods = "\n".join(f"- {m.name}: {m.summary}" for m in modules[:40])
    prompt = (
        f"Write a comprehensive developer handbook in GitHub-flavored Markdown for the project "
        f"'{index.name}'. Use these sections as H1 headings (#): Executive Summary, Project Overview, "
        f"Architecture, Technology Stack, Project Structure, Key Components, Data Flow, "
        f"Setup & Installation, Developer Onboarding, Common Tasks, Gotchas. Be concrete and specific. "
        f"Use bold and bullet lists. Do not wrap the whole thing in a code fence.\n\n"
        f"Project summary: {index.summary}\n\nModules:\n{mods}\n\n"
        f"Entry points: {', '.join(index.entry_points) or 'unknown'}\n"
        f"Languages: {', '.join(meta_langs(index))}"
    )
    try:
        text = await llm.complete(prompt, LLMCompletionOptions(temperature=0.4, max_tokens=8000))
        return text.strip() or _heuristic_markdown(index, modules)
    except Exception:
        return _heuristic_markdown(index, modules)


def meta_langs(index) -> list[str]:
    return sorted(index.languages, key=lambda k: -index.languages[k])


def _heuristic_markdown(index, modules) -> str:
    lines = [f"# Executive Summary", "", index.summary, "",
             f"# Project Overview", "",
             f"**{index.name}** is a {', '.join(meta_langs(index))} project with "
             f"{index.file_count} files and about {index.total_loc:,} lines of code, "
             f"organized into {len(modules)} modules.", "",
             f"# Architecture", "",
             "The codebase is organized into these modules:", ""]
    for m in modules:
        dep = f" — depends on {', '.join(m.depends_on)}" if m.depends_on else ""
        lines.append(f"- **{m.name}**{dep}: {m.summary}")
    lines += ["", "# Technology Stack", "",
              ", ".join(f"{k} ({v} files)" for k, v in sorted(index.languages.items(), key=lambda x: -x[1])),
              "", "# Project Structure", ""]
    for m in modules:
        lines.append(f"## {m.name}")
        for f in m.files[:12]:
            lines.append(f"- `{f.path}`" + (f" — {f.summary}" if f.summary else ""))
        lines.append("")
    lines += ["# Developer Onboarding", "",
              "Start with these entry points:", ""]
    for e in (index.entry_points or [m.name for m in modules[:3]]):
        lines.append(f"- `{e}`")
    return "\n".join(lines)
