# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""Write the spoken narration for the video — one batched Gemini call, length-targeted."""
from __future__ import annotations

import json
import re

from oneport_context.video.plan import words_per_scene


async def build_scripts(index, scenes: list[dict], llm=None, minutes: float = 8.0) -> list[str]:
    """One spoken narration string per scene, sized so the total video ≈ `minutes`."""
    wps = words_per_scene(len(scenes), minutes)
    if llm is None:
        # Heuristic: no model → use the seed notes (video will be shorter than the target).
        return [s["note"] for s in scenes]

    from oneport_debug_core.llm.base import LLMCompletionOptions
    manifest = "\n\n".join(f"{i}. [{s['title']}]\n{s['note']}" for i, s in enumerate(scenes))
    prompt = (
        f"You are the narrator of a {int(minutes)}-minute code-walkthrough video for the project "
        f"'{index.name}'. For EACH numbered section below, write natural, spoken narration of "
        f"about {wps} words — conversational, specific, and technical, for a developer audience. "
        f"Expand each with concrete detail (what the code does, why it matters, the key files, how "
        f"it connects to the rest). Do not just restate the note. "
        f"Return ONLY a JSON array of strings — one per section, in order. No markdown.\n\n"
        f"Sections:\n{manifest}"
    )
    try:
        raw = await llm.complete(prompt, LLMCompletionOptions(temperature=0.5, max_tokens=8000))
        arr = _extract_json_array(raw)
        if isinstance(arr, list) and len(arr) == len(scenes):
            return [str(x).strip() or scenes[i]["note"] for i, x in enumerate(arr)]
        _warn_fallback(
            f"model returned {'a mismatched list' if isinstance(arr, list) else 'no JSON array'}"
        )
    except Exception as exc:  # noqa: BLE001 — never fail the render over narration
        # A silent fallback here produced an identical canned-template video every
        # time the model was rate-limited, with no way to tell why. Say it out loud.
        _warn_fallback(str(exc).strip() or exc.__class__.__name__)
    return [s["note"] for s in scenes]


def _warn_fallback(reason: str) -> None:
    from oneport_debug_core.cli.output import console

    console.print(
        f"  [yellow]Narration fell back to the built-in template[/yellow] [dim]({reason})[/dim]\n"
        f"  [dim]The video still renders, but the script is generic. Re-run to get "
        f"AI narration.[/dim]"
    )


def _extract_json_array(raw: str):
    s = re.sub(r"^\s*```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    s = re.sub(r"\s*```\s*$", "", s)
    first, last = s.find("["), s.rfind("]")
    if first != -1 and last > first:
        return json.loads(s[first:last + 1])
    return None
