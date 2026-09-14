# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Chat retrieval + answer.

Grounded, and quality-first. Two-stage retrieval:

  1. ROUTE — hand the model the manifest (every file + what it does + its top
     symbols) and ask which files are needed to answer the question. The model
     is far better than keyword overlap at "how does billing work?" → charge.py,
     even when the word "billing" never appears. This is where answer quality is
     won or lost.
  2. READ — pull those real files off disk in full (nothing was uploaded; the
     code is right here) and let the model answer, citing real paths.

Keyword scoring is kept as a fast fallback: it shortlists candidates when the
manifest is too big to route in one shot, and it answers when no model is
available. So the router only ever makes things better, never worse.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from oneport_context.models import FileNode, RepoIndex

_FILE_CAP = 8000            # chars per file in the answer prompt
_CTX_BUDGET = 60_000        # total chars of code context in the answer prompt
_ROUTE_BUDGET = 45_000      # chars of manifest we'll route over in one shot
_SHORTLIST = 80            # when the manifest is too big, keyword-shortlist to this many


# ── keyword retrieval (fast fallback) ────────────────────────────────────────

def _keyword_rank(index: RepoIndex, question: str) -> list[FileNode]:
    tokens = set(re.findall(r"\w+", question.lower()))
    scored = []
    for f in index.all_files():
        blob = f"{f.path} {f.summary} {' '.join(s.name for s in f.symbols)}".lower()
        score = sum(3 if t in f.path.lower() else 1 for t in tokens if t in blob)
        scored.append((score, f))
    scored.sort(key=lambda x: -x[0])
    return [f for s, f in scored if s > 0] or [f for _, f in scored]


def retrieve(index: RepoIndex, question: str, k: int = 15) -> tuple[str, list[str]]:
    """Keyword-only retrieval: (code_context, cited_paths). The offline fallback."""
    ranked = _keyword_rank(index, question)[:k] or index.all_files()[:5]
    return _read_files(Path(index.root), ranked)


# ── AI router (quality path) ─────────────────────────────────────────────────

def _manifest_line(f: FileNode) -> str:
    syms = ", ".join(s.name for s in f.symbols[:6])
    desc = (f.summary or "").strip() or syms or f.language
    return f"- {f.path} :: {desc[:160]}" + (f"  [{syms}]" if syms and f.summary else "")


async def route(index: RepoIndex, question: str, llm, want: int = 12) -> list[str]:
    """Ask the model which files are needed to answer `question`.

    Returns repo-relative paths (validated against the index), most relevant
    first. Empty list on any failure — the caller then falls back to keyword."""
    from oneport_debug_core.llm.base import LLMCompletionOptions

    files = index.all_files()
    valid = {f.path for f in files}

    # Build the manifest to route over. If it fits, route over the whole repo
    # (best quality). If not, keyword-shortlist first so we still scale to a
    # huge repo — that's the point where embeddings would take over.
    lines, total = [], 0
    candidates = files
    manifest = "\n".join(_manifest_line(f) for f in files)
    if len(manifest) > _ROUTE_BUDGET:
        candidates = _keyword_rank(index, question)[:_SHORTLIST]
    for f in candidates:
        line = _manifest_line(f)
        if total + len(line) > _ROUTE_BUDGET:
            break
        lines.append(line)
        total += len(line) + 1
    if not lines:
        return []

    prompt = (
        f"You are routing a question to the right source files in the repo "
        f"'{index.name}'. Below is every file and what it does.\n\n"
        f"FILES:\n" + "\n".join(lines) + "\n\n"
        f"QUESTION: {question}\n\n"
        f"Return ONLY a JSON array of the file paths needed to answer this, most "
        f"relevant first, at most {want}. Copy paths exactly. No prose.\n"
        f'Example: ["src/a.py", "src/b.py"]'
    )
    try:
        raw = await llm.complete(prompt, LLMCompletionOptions(temperature=0.0, max_tokens=400))
    except Exception:
        return []
    return _parse_paths(raw, valid)[:want]


def _parse_paths(raw: str, valid: set[str]) -> list[str]:
    s = re.sub(r"^```(?:json)?", "", (raw or "").strip())
    s = re.sub(r"```$", "", s).strip()
    picks: list[str] = []
    m = re.search(r"\[.*\]", s, re.DOTALL)
    if m:
        try:
            picks = [str(x) for x in json.loads(m.group(0)) if isinstance(x, str)]
        except Exception:
            picks = []
    if not picks:                       # tolerate a plain list, one path per line
        picks = [ln.strip("-*• \t\"'") for ln in s.splitlines()]
    out: list[str] = []
    for p in picks:
        p = p.replace("\\", "/").strip().strip("\"'")
        if p in valid and p not in out:
            out.append(p)
    return out


async def retrieve_smart(index: RepoIndex, question: str, llm, want: int = 12) -> tuple[str, list[str]]:
    """Router-first retrieval; falls back to keyword if the router finds nothing."""
    files = index.all_files()
    # Tiny repos: routing is pointless, just read everything.
    if len(files) > want:
        paths = await route(index, question, llm, want=want)
        by_path = {f.path: f for f in files}
        picks = [by_path[p] for p in paths if p in by_path]
        if picks:
            return _read_files(Path(index.root), picks)
    else:
        picks = files
        return _read_files(Path(index.root), picks)
    # router came back empty → keyword fallback (never worse than before)
    return retrieve(index, question, k=want)


# ── shared read + answer ─────────────────────────────────────────────────────

def _read_files(root: Path, files: list[FileNode]) -> tuple[str, list[str]]:
    parts, cited, used = [], [], 0
    for f in files:
        content = _read(root / f.path)
        if not content:
            continue
        block = f"\n### {f.path}  [{f.language}]\n{content[:_FILE_CAP]}\n"
        if used + len(block) > _CTX_BUDGET:
            break
        parts.append(block)
        cited.append(f.path)
        used += len(block)
    return "".join(parts), cited


async def answer(index: RepoIndex, llm, history: list[tuple[str, str]], question: str) -> tuple[str, list[str]]:
    """Answer `question` about the repo, grounded in retrieved files. Returns (answer, cited)."""
    from oneport_debug_core.llm.base import LLMCompletionOptions

    if llm is not None:
        context, cited = await retrieve_smart(index, question, llm)
    else:
        context, cited = retrieve(index, question)

    convo = "\n".join(f"Q: {q}\nA: {a}" for q, a in history[-4:])
    prompt = (
        f"You are an expert on the codebase '{index.name}'. Answer the question using the "
        f"actual source files below. Be precise and technical, and cite specific file paths. "
        f"If the files don't contain the answer, say so plainly rather than guessing.\n\n"
        f"=== Relevant files ===\n{context}\n\n"
        + (f"=== Conversation so far ===\n{convo}\n\n" if convo else "")
        + f"=== Question ===\n{question}\n\nAnswer:"
    )
    text = await llm.complete(prompt, LLMCompletionOptions(temperature=0.2, max_tokens=1500))
    return text.strip(), cited


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
