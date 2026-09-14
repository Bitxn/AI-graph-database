# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Hierarchical indexer — the scalability engine.

Builds understanding bottom-up: files (cheap heuristic) → modules (one LLM call
each) → whole repo (one LLM call). This map-reduce is what lets it handle a
monorepo without stuffing everything into one prompt, and it degrades to a fully
heuristic index when no model is configured (free, offline, still useful).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from oneport_context.models import FileNode, ModuleNode, RepoIndex
from oneport_context.scan import ScanConfig
from oneport_context.walker import group_modules, walk_repo

_ENTRY_HINTS = ("main.py", "__main__.py", "app.py", "cli.py", "manage.py",
                "index.js", "index.ts", "main.go", "server.js", "server.ts", "main.js")


def _fingerprint(files: list[FileNode]) -> str:
    """A single digest of the scanned file set — a fast 'did anything change?'
    check that complements the per-file hashes used to itemise changes."""
    joined = "\n".join(f"{f.path}:{f.content_hash}" for f in sorted(files, key=lambda x: x.path))
    return hashlib.sha1(joined.encode("utf-8", errors="replace")).hexdigest()


async def build_index(root: Path, llm=None, model_name: str = "heuristic",
                      scan: ScanConfig | None = None) -> RepoIndex:
    """Walk the repo and summarize it hierarchically. `llm` is an optional LLMRouter."""
    root = Path(root).resolve()
    scan = scan or ScanConfig()
    files = walk_repo(root, scan)
    modules = group_modules(files)

    languages: dict[str, int] = {}
    for f in files:
        languages[f.language] = languages.get(f.language, 0) + 1

    index = RepoIndex(
        name=root.name,
        root=str(root),
        modules=modules,
        entry_points=_entry_points(files),
        languages=languages,
        file_count=len(files),
        total_loc=sum(f.loc for f in files),
        model_used=model_name if llm else "heuristic",
        fingerprint=_fingerprint(files),
        truncated=scan.truncated,
    )

    # Module summaries (bottom of the map-reduce). Batched so a big repo costs a
    # handful of calls, not one-per-module (which blows the free-tier 5/min limit).
    if llm:
        await _summarize_modules_batched(modules, llm)
    else:
        for m in modules:
            m.summary = _heuristic_module(m)

    # Repo summary (top).
    readme = _read_readme(root)
    index.summary = await _summarize_repo(index, readme, llm) if llm else _heuristic_repo(index, readme)
    return index


# --------------------------------------------------------------------------- #
# Heuristic (no-LLM) summaries                                                  #
# --------------------------------------------------------------------------- #

def _summary_is_descriptive(s: str) -> bool:
    """A file summary worth showing as the module's summary — a real description,
    not a code fragment or shell command. An honest file listing beats a
    misleading fragment.

    The uppercase-start check is the cheap prose signal that separates a
    description ("Serialize...", "This module...") from a lifted code comment
    ("openssl rand -hex 32", "to get a string like this run:") — both of which
    slipped through as module summaries on fastapi/docs_src/security.
    """
    s = (s or "").strip()
    if len(s) < 20 or len(s.split()) < 3:
        return False
    if s.endswith((":", "=", ",", "-", "(")):
        return False
    return s[:1].isupper()


def _heuristic_module(m: ModuleNode) -> str:
    # Prefer the most *representative* file's docstring (an __init__ or a file named
    # like the module), not just the longest — then fall back to a file listing.
    base = m.name.split("/")[-1].lower()

    def rank(f):
        name = f.path.split("/")[-1].lower()
        return (name.startswith("__init__"), base and base in name, len(f.summary or ""))

    docced = [f for f in m.files if _summary_is_descriptive(f.summary)]
    if docced:
        return max(docced, key=rank).summary[:220]
    langs = sorted({f.language for f in m.files})
    key = ", ".join(f.path.split("/")[-1] for f in m.files[:4])
    return f"{len(m.files)} {'/'.join(langs)} file(s): {key}"


_MARKUP_START = ("<", "..", "![", "[![", "|", ":", "```", "---", "===", "***", "|-")


def _is_prose(para: str) -> bool:
    """True for a paragraph that actually describes the project.

    READMEs almost never open with prose: saleor starts with a `<picture>` block,
    scrapy with an RST `.. |logo| image::` directive. Both are >40 chars, so a
    naive "first long paragraph" wins the markup and prints it as the project
    summary — at the top of every index, handbook, PDF and video.
    """
    s = para.strip()
    if not s or s.startswith(_MARKUP_START):
        return False

    # A bulleted/numbered block is a table of contents or a feature list, not a
    # description — saleor's README opens with a TOC of markdown links, which
    # survives link-stripping as perfectly good "words".
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if lines and sum(bool(re.match(r"^([-*+]|\d+[.)])\s", ln)) for ln in lines) / len(lines) > 0.5:
        return False

    # Strip badges, images, links (keeping link text), inline HTML and code, then
    # insist what remains is mostly words — a wall of shields.io badges isn't prose.
    bare = re.sub(r"\[!\[[^\]]*\]\([^)]*\)|!\[[^\]]*\]\([^)]*\)", "", s)   # badges/images
    bare = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", bare)                    # [text](url) -> text
    bare = re.sub(r"<[^>]+>|https?://\S+|`[^`]*`", "", bare).strip()
    if len(bare) < 40:
        return False
    wordish = sum(ch.isalpha() or ch.isspace() for ch in bare)
    return wordish / len(bare) > 0.75


def _heuristic_repo(index: RepoIndex, readme: str) -> str:
    if readme:
        for para in readme.split("\n\n"):
            p = para.strip().lstrip("#").strip()
            if _is_prose(p):
                return p[:400]
    top = ", ".join(m.name for m in index.modules[:6])
    langs = ", ".join(f"{k} ({v})" for k, v in sorted(index.languages.items(), key=lambda x: -x[1]))
    return f"A {langs} project with {index.file_count} files across modules: {top}."


# --------------------------------------------------------------------------- #
# LLM summaries                                                                 #
# --------------------------------------------------------------------------- #

_BATCH_SIZE = 12          # modules described per LLM call
_BATCH_PACING_S = 1.5     # brief gap between calls to respect free-tier limits


async def _summarize_modules_batched(modules: list[ModuleNode], llm) -> None:
    """Describe modules in batches — ~len(modules)/12 calls instead of one each."""
    import asyncio
    from oneport_debug_core.llm.base import LLMCompletionOptions

    for start in range(0, len(modules), _BATCH_SIZE):
        batch = modules[start:start + _BATCH_SIZE]
        manifest = "\n\n".join(_module_manifest(m) for m in batch)
        prompt = (
            "For EACH module below, write a 1-2 sentence description of its ROLE — what it is "
            "responsible for. Return ONLY a JSON array of objects "
            '[{"name": "<module name>", "summary": "<1-2 sentences>"}], one per module, in order. '
            "No markdown, no preamble.\n\n"
            f"{manifest}"
        )
        try:
            raw = await llm.complete(prompt, LLMCompletionOptions(temperature=0.2, max_tokens=2000))
            by_name = _parse_named_summaries(raw)
            for m in batch:
                m.summary = (by_name.get(m.name) or "").strip()[:400] or _heuristic_module(m)
        except Exception:
            for m in batch:
                m.summary = _heuristic_module(m)
        if start + _BATCH_SIZE < len(modules):
            await asyncio.sleep(_BATCH_PACING_S)


def _module_manifest(m: ModuleNode) -> str:
    files = "\n".join(
        f"  - {f.path} ({f.language}): {f.summary or 'n/a'} [{', '.join(s.name for s in f.symbols[:5])}]"
        for f in m.files[:15]
    )
    return f"MODULE: {m.name}\n{files}"


def _parse_named_summaries(raw: str) -> dict[str, str]:
    import json
    import re
    s = re.sub(r"^\s*```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    s = re.sub(r"\s*```\s*$", "", s)
    first, last = s.find("["), s.rfind("]")
    if first == -1 or last <= first:
        return {}
    try:
        arr = json.loads(s[first:last + 1])
    except Exception:
        return {}
    return {it["name"]: it.get("summary", "") for it in arr
            if isinstance(it, dict) and it.get("name")}


async def _summarize_repo(index: RepoIndex, readme: str, llm) -> str:
    from oneport_debug_core.llm.base import LLMCompletionOptions
    mods = "\n".join(f"- {m.name}: {m.summary}" for m in index.modules[:30])
    readme_hint = f"\nREADME excerpt:\n{readme[:1500]}" if readme else ""
    prompt = (
        f"You are onboarding a new engineer. In 2-3 plain sentences, explain what the project "
        f"'{index.name}' does and how it's structured. No preamble, no markdown.\n\n"
        f"Modules:\n{mods}{readme_hint}"
    )
    try:
        text = await llm.complete(prompt, LLMCompletionOptions(temperature=0.2, max_tokens=300))
        return text.strip().strip('"')[:600] or _heuristic_repo(index, readme)
    except Exception:
        return _heuristic_repo(index, readme)


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def _entry_points(files: list[FileNode]) -> list[str]:
    hits = [f.path for f in files if f.path.split("/")[-1] in _ENTRY_HINTS]
    return hits[:10]


def _read_readme(root: Path) -> str:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = root / name
        if p.exists():
            try:
                return p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return ""
