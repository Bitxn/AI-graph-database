# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Plan the video's scenes from the index — richer than the browser walkthrough so a
full narrated video runs long enough to be useful (target ≥ 8 min).

Each scene carries a `note` (the seed content); the narration for it is written by
the script step, expanded to a per-scene word budget so the total hits the target.
"""
from __future__ import annotations


def plan_video_scenes(index) -> list[dict]:
    modules = sorted(index.modules, key=lambda m: -m.loc)
    langs = ", ".join(k for k, _ in sorted(index.languages.items(), key=lambda x: -x[1])[:5])
    entry = ", ".join(index.entry_points[:5]) or "the main entry files"

    scenes: list[dict] = [
        {"kind": "cover", "highlight": None, "title": index.name,
         "note": f"Title card for the project '{index.name}'. Welcome the viewer."},
        {"kind": "overview", "highlight": None, "title": "What it does",
         "note": f"Overview of {index.name}: {index.summary} "
                 f"It has {index.file_count} files and about {index.total_loc} lines, "
                 f"written in {langs}."},
        {"kind": "architecture", "highlight": None, "title": "Architecture",
         "note": f"The codebase is organized into {len(modules)} modules: "
                 + "; ".join(f"{m.name}" + (f" (depends on {', '.join(m.depends_on)})" if m.depends_on else "")
                             for m in modules)
                 + ". Explain how they fit together."},
    ]

    for m in modules:
        files = ", ".join(f.path.split("/")[-1] for f in m.files[:6])
        scenes.append({
            "kind": "module", "highlight": m.name, "title": m.name,
            "note": f"The '{m.name}' module: {m.summary} "
                    f"Key files: {files}. "
                    + (f"It depends on {', '.join(m.depends_on)}. " if m.depends_on else "")
                    + "Explain its responsibility and how a developer would work with it.",
        })

    scenes.append({
        "kind": "files", "highlight": None, "title": "Where to start",
        "note": f"For a new developer, the entry points are: {entry}. "
                f"Explain what to read first to understand this codebase and how to get oriented.",
    })
    scenes.append({
        "kind": "outro", "highlight": None, "title": "Recap",
        "note": f"Recap the purpose and structure of {index.name}, and mention that you can "
                f"hover any symbol in your editor with oneport-context lsp, or ask questions "
                f"with oneport-context chat.",
    })
    return scenes


def words_per_scene(num_scenes: int, minutes: float) -> int:
    """Words each scene's narration should target so the total ≈ `minutes` (gTTS ≈ 150 wpm)."""
    total = int(minutes * 160)                 # aim slightly high to clear the minimum
    per = total // max(1, num_scenes)
    return max(70, min(220, per))
