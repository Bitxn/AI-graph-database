# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""Orchestrate the MP4 walkthrough: plan → script → slides → voice → stitch."""
from __future__ import annotations

import tempfile
from pathlib import Path

from oneport_debug_core.cli.output import console
from oneport_context.video.plan import plan_video_scenes
from oneport_context.video.script import build_scripts
from oneport_context.video.slides import render_slide
from oneport_context.video.tts import build_voicer
from oneport_context.video.render import build_video


async def make_video(index, storyboard: dict, out_mp4: Path,
                     api_key: str | None = None, llm=None,
                     voice: str = "us", minutes: float = 8.0) -> tuple[Path, str]:
    """Produce a narrated MP4 walkthrough (target ≈ `minutes`). Returns (mp4, voice_engine)."""
    modules = storyboard["modules"]
    scenes = plan_video_scenes(index)

    console.print(f"  [dim]Writing narration ({len(scenes)} scenes · target ~{int(minutes)} min)…[/dim]")
    scripts = await build_scripts(index, scenes, llm=llm, minutes=minutes)

    say, engine_used = build_voicer(api_key=api_key, voice=voice)
    console.print(f"  [dim]voice: {engine_used}[/dim]")

    work = Path(tempfile.mkdtemp(prefix="oneport-video-"))
    items: list[tuple[Path, Path]] = []
    for i, (scene, script) in enumerate(zip(scenes, scripts)):
        png = work / f"slide_{i:02d}.png"
        _render(scene, index, modules, script, png)
        audio = say(script, work / f"audio_{i:02d}")
        items.append((png, audio))
        console.print(f"  [dim]scene {i+1}/{len(scenes)}: {scene['title']}[/dim]")

    console.print("  [dim]Rendering video (ffmpeg)…[/dim]")
    build_video(items, out_mp4)
    return out_mp4, engine_used


def _render(scene: dict, index, modules: list[dict], caption: str, png: Path) -> None:
    kind = scene["kind"]
    repo = index.name
    langs = ", ".join(k for k, _ in sorted(index.languages.items(), key=lambda x: -x[1])[:4])

    if kind == "cover":
        render_slide(png, kind="cover", title=index.name, subtitle=index.summary,
                     caption=caption, repo=repo)
    elif kind == "overview":
        bullets = [f"{index.file_count} files · {index.total_loc:,} lines",
                   f"Languages: {langs}",
                   f"Modules: {len(modules)}"]
        render_slide(png, kind="content", title="What it does",
                     subtitle=index.summary, bullets=bullets, caption=caption, repo=repo)
    elif kind == "architecture":
        render_slide(png, kind="architecture", title="Architecture",
                     caption=caption, repo=repo, modules=modules)
    elif kind == "module":
        render_slide(png, kind="architecture", title=scene["title"],
                     caption=caption, repo=repo, modules=modules, highlight=scene["highlight"])
    elif kind == "files":
        bullets = index.entry_points[:6] or [m["name"] for m in modules[:6]]
        render_slide(png, kind="content", title="Where to start",
                     bullets=bullets, caption=caption, repo=repo)
    else:  # outro
        render_slide(png, kind="cover", title="Recap", subtitle=index.name,
                     caption=caption, repo=repo)
