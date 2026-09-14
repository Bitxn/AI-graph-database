# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Storyboard — turns a RepoIndex into a narrated scene sequence.

The walkthrough is a presenter walking a single architecture diagram: a cover,
the map, one scene per major module (highlighting its box while narrating its
role), then an outro. Built deterministically from the index's existing summaries
so `show` is instant and free after indexing — the LLM already did its work at
index time.
"""
from __future__ import annotations

from oneport_context.models import RepoIndex

_MAX_MODULE_SCENES = 7


async def build_storyboard(index: RepoIndex, llm=None) -> dict:
    """Return a JSON-serializable walkthrough payload the browser renders."""
    modules = sorted(index.modules, key=lambda m: -m.loc)
    top = modules[:_MAX_MODULE_SCENES]

    modules_payload = [
        {
            "name": m.name,
            "summary": m.summary,
            "loc": m.loc,
            "depends_on": m.depends_on,
            "files": [
                {"path": f.path, "summary": f.summary,
                 "symbols": [s.name for s in f.symbols[:8]], "language": f.language}
                for f in m.files[:12]
            ],
        }
        for m in modules
    ]

    langs = ", ".join(f"{k}" for k, _ in sorted(index.languages.items(), key=lambda x: -x[1])[:4])
    scenes: list[dict] = []

    # 1. Cover
    scenes.append({
        "kind": "cover", "highlight": None,
        "title": index.name,
        "narration": index.summary or f"{index.name}: a {langs} project.",
    })

    # 2. The map
    dep_count = sum(len(m.depends_on) for m in modules)
    scenes.append({
        "kind": "architecture", "highlight": None,
        "title": "The architecture",
        "narration": (
            f"{index.name} is organized into {len(modules)} modules, "
            f"{langs}, {index.file_count} files and about {index.total_loc:,} lines of code. "
            + (f"Here's how they connect." if dep_count else "Here's the module map.")
        ),
    })

    # 3..n. One scene per major module
    for m in top:
        files_line = ", ".join(f["path"].split("/")[-1] for f in
                               next(mp["files"] for mp in modules_payload if mp["name"] == m.name)[:3])
        dep_line = (f" It builds on {', '.join(m.depends_on)}." if m.depends_on else "")
        scenes.append({
            "kind": "module", "highlight": m.name,
            "title": m.name,
            "narration": f"{m.summary}{dep_line} Key files: {files_line}.",
        })

    # last. Outro
    scenes.append({
        "kind": "outro", "highlight": None,
        "title": "Go deeper",
        "narration": (
            "That's the tour. Hover any symbol in your editor for an explanation with "
            "oneport-context lsp, or ask questions with oneport-context chat."
        ),
    })

    return {
        "repo": index.name,
        "summary": index.summary,
        "brain": index.model_used,
        "stats": {"files": index.file_count, "loc": index.total_loc,
                  "modules": len(modules), "languages": index.languages},
        "entry_points": index.entry_points,
        "modules": modules_payload,
        "scenes": scenes,
    }
