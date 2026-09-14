"""
repomap.py — the inbuilt repo-intelligence engine (the app's own context brain).

Builds a living map of a repo and keeps it latest:
  • manifest.json  — per-file AI summary + role (what each file does)
  • README         — a generated repo overview, grounded in the manifest
  • mind-map       — a tree the UI renders (dirs → files → role + summary)

It updates INCREMENTALLY: on every change only the changed files are
re-summarized (content-hash compared), so after each Claude Code / Codex edit
the context refreshes cheaply instead of re-reading the whole repo.

Uses the OnePort managed model, or a bring-your-own Gemini key. Stored per
project in %APPDATA%/OnePort/manifests/{pid}.json.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from projects import DATA_DIR, IGNORE_DIRS

MANIFEST_DIR = DATA_DIR / "manifests"
DEFAULT_MODEL = "gemini-2.5-flash"

# Text/code files worth summarising. Everything else (binaries, media) is skipped.
CODE_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java",
    ".rb", ".php", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".swift", ".kt",
    ".scala", ".sh", ".bash", ".sql", ".html", ".css", ".scss", ".vue", ".svelte",
    ".md", ".rst", ".yaml", ".yml", ".toml", ".json", ".txt", ".cfg", ".ini",
    ".gradle", ".dart", ".lua", ".r", ".ex", ".exs", ".proto", ".graphql",
}
SPECIAL_NAMES = {"Dockerfile", "Makefile", "Procfile", ".gitignore", "requirements.txt"}
MAX_FILE_CHARS = 6000     # truncate big files before summarising
MAX_FILES = 500           # bound a first full build
MAX_SUMMARY_TOKENS = 160

ROLES = ("entrypoint", "api", "model", "ui", "config", "test", "util",
         "docs", "build", "data", "schema", "other")


# ── LLM (managed proxy, or BYOK Gemini) ──────────────────────────────────────
def _llm(system: str, user: str, model: str, gemini_key: str | None,
         max_tokens: int, json_mode: bool = True) -> tuple[str, int]:
    # No BYOK: every AI call goes through the OnePort managed proxy. The client
    # never holds a Gemini key (nothing to leak); the keys live only on the
    # backend, which spreads load + fails over across its key pool.
    from oneport_account import call_managed_llm
    r = call_managed_llm(system=system, user=user, tool="context", action="map",
                         model=model, max_tokens=max_tokens, temperature=0.1,
                         response_json=json_mode, thinking_budget=0 if json_mode else None,
                         timeout=120)
    return r.text, r.tokens_used


def _llm_byok(system, user, model, key, max_tokens, json_mode):
    import httpx
    model_id = {"gemini-flash-latest": "gemini-2.5-flash"}.get(model, model)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent"
    gen = {"temperature": 0.1, "maxOutputTokens": max_tokens}
    if json_mode:
        gen["responseMimeType"] = "application/json"
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen}
    resp = httpx.post(url, params={"key": key}, json=body, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    return text, int(data.get("usageMetadata", {}).get("totalTokenCount", 0))


def _parse_json(raw: str):
    raw = (raw or "").strip()
    if "```" in raw:
        seg = raw.split("```", 2)
        raw = seg[1] if len(seg) > 1 else raw
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    i, j = raw.find("{"), raw.rfind("}")
    if i == -1 or j <= i:
        return None
    try:
        return json.loads(raw[i:j + 1])
    except json.JSONDecodeError:
        return None


# ── manifest store ───────────────────────────────────────────────────────────
def _manifest_path(pid: str) -> Path:
    return MANIFEST_DIR / f"{pid}.json"


def load_manifest(pid: str) -> dict:
    p = _manifest_path(pid)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"files": {}, "readme": "", "generated_at": None, "tokens": 0}


def save_manifest(pid: str, m: dict) -> None:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    _manifest_path(pid).write_text(json.dumps(m, indent=2), encoding="utf-8")


# ── file discovery ───────────────────────────────────────────────────────────
def _is_source(name: str) -> bool:
    return name in SPECIAL_NAMES or Path(name).suffix.lower() in CODE_EXT


def _source_files(root: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for f in filenames:
            if _is_source(f):
                out.append(os.path.join(dirpath, f))
        if len(out) > MAX_FILES * 2:
            break
    return out[:MAX_FILES]


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
    except OSError:
        return ""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


# ── summarise one file ───────────────────────────────────────────────────────
_SUM_SYS = (
    "You map a code repository. Given a file path and its (possibly truncated) "
    "content, describe what the file does — grounded ONLY in what you see. Output "
    'strict JSON: {"role":"<one of: ' + "|".join(ROLES) + '>", "summary":"<one '
    'precise sentence>"}. If the content is too little to tell, say so in summary '
    'and use role "other".'
)


def _summarize(rel: str, content: str, model: str, gemini_key: str | None):
    user = f"path: {rel}\n\ncontent:\n{content or '(empty)'}"
    raw, tokens = _llm(_SUM_SYS, user, model, gemini_key, MAX_SUMMARY_TOKENS)
    data = _parse_json(raw) or {}
    role = str(data.get("role", "other")).lower()
    if role not in ROLES:
        role = "other"
    return {"role": role, "summary": str(data.get("summary", "")).strip()[:240]}, tokens


# ── build / update ───────────────────────────────────────────────────────────
def build(pid: str, root: str, changed: list[str] | None = None,
          model: str = DEFAULT_MODEL, gemini_key: str | None = None) -> dict:
    """Build or incrementally update the manifest.

    changed=None → full pass (re-summarise any file whose hash changed or is new).
    changed=[names] → only (re)summarise those files (the fast per-edit path)."""
    from datetime import datetime, timezone

    m = load_manifest(pid)
    files = _source_files(root)
    rel_of = {f: os.path.relpath(f, root).replace("\\", "/") for f in files}

    # decide which files to (re)summarise
    if changed is not None:
        wanted = {r for r in (n.replace("\\", "/") for n in changed)}
        targets = [f for f in files if rel_of[f] in wanted or os.path.basename(f) in wanted]
    else:
        targets = files

    updated, tokens = 0, 0
    for f in targets:
        rel = rel_of[f]
        content = _read(f)
        h = _hash(content)
        prev = m["files"].get(rel)
        if prev and prev.get("hash") == h and prev.get("summary"):
            continue  # unchanged — skip the AI call
        try:
            info, t = _summarize(rel, content, model, gemini_key)
        except Exception as exc:
            info, t = {"role": "other", "summary": f"(not analysed: {exc})"}, 0
        m["files"][rel] = {**info, "hash": h,
                           "updated": datetime.now(timezone.utc).isoformat()}
        updated += 1
        tokens += t

    # drop files that no longer exist
    live = set(rel_of.values())
    for gone in [r for r in m["files"] if r not in live]:
        del m["files"][gone]

    m["generated_at"] = datetime.now(timezone.utc).isoformat()
    m["tokens"] = int(m.get("tokens", 0)) + tokens
    m["file_count"] = len(m["files"])
    save_manifest(pid, m)
    return {"updated": updated, "tokens": tokens, "file_count": len(m["files"])}


# ── README ───────────────────────────────────────────────────────────────────
_README_SYS = (
    "You are given a map of a repository: each file with its role and a one-line "
    "summary. Write a concise, accurate README overview in Markdown: what the "
    "project is, its structure, the key modules and how they fit together. Ground "
    "everything ONLY in the provided map — never invent features, commands, or "
    "files not present. Keep it under ~400 words."
)


def generate_readme(pid: str, model: str = DEFAULT_MODEL,
                    gemini_key: str | None = None) -> dict:
    m = load_manifest(pid)
    if not m["files"]:
        return {"error": "Build the map first."}
    lines = [f"- {r} [{i['role']}] — {i['summary']}"
             for r, i in sorted(m["files"].items())]
    user = "Repository file map:\n" + "\n".join(lines[:MAX_FILES])
    try:
        raw, tokens = _llm(_README_SYS, user, model, gemini_key, 1200, json_mode=False)
    except Exception as exc:
        return {"error": f"README generation failed: {exc}"}
    m["readme"] = raw.strip()
    m["tokens"] = int(m.get("tokens", 0)) + tokens
    save_manifest(pid, m)
    return {"tokens": tokens, "chars": len(m["readme"])}


_INTENT_SYS = (
    "You are drafting an INTENT specification for a software repository — a concise, "
    "behaviour-focused statement of how the software is SUPPOSED to work, used as a "
    "baseline to check future changes against. From the overview + file map, write "
    "Markdown with exactly these sections:\n"
    "# Intent\n"
    "## Purpose\n(2-3 sentences: what the software is for.)\n"
    "## Expected behaviours\n(bulleted list of the key behaviours/guarantees it should "
    "uphold — auth, data handling, API contracts, error handling, performance.)\n"
    "## Must not\n(bulleted list of things it should NOT do.)\n"
    "Base it ONLY on the provided context. Keep it high-level and behavioural, NOT an "
    "implementation description. This is an AI-drafted starting point to be refined."
)

_INTENT_TEMPLATE = """# Intent

> AI could not draft this automatically — fill it in. This file states how your
> software is SUPPOSED to behave, so OnePort can check changes against it.

## Purpose
<!-- 2-3 sentences: what is this software for? -->

## Expected behaviours
- <!-- e.g. All API endpoints require authentication -->
- <!-- e.g. User data is never logged in plaintext -->

## Must not
- <!-- e.g. Must not break the public API without a version bump -->
"""


def generate_intent(pid: str, model: str = DEFAULT_MODEL,
                    gemini_key: str | None = None) -> dict:
    """Draft an intent.md for the repo from its codebase map. Always returns
    Markdown — falls back to a fill-in template if the map/AI isn't available."""
    m = load_manifest(pid)
    if not m.get("files"):
        return {"markdown": _INTENT_TEMPLATE, "tokens": 0, "source": "template"}
    ctx = ""
    if m.get("readme"):
        ctx += "OVERVIEW:\n" + m["readme"][:1500] + "\n\n"
    lines = [f"- {r} [{i['role']}] — {i['summary']}"
             for r, i in sorted(m["files"].items())]
    ctx += "FILE MAP:\n" + "\n".join(lines[:300])
    try:
        raw, tokens = _llm(_INTENT_SYS, ctx, model, gemini_key, 1000, json_mode=False)
        md = raw.strip()
        if not md:
            return {"markdown": _INTENT_TEMPLATE, "tokens": tokens, "source": "template"}
        return {"markdown": md, "tokens": tokens, "source": "ai"}
    except Exception:
        return {"markdown": _INTENT_TEMPLATE, "tokens": 0, "source": "template"}


# ── mind-map tree (deterministic, from the manifest) ─────────────────────────
def mindmap(pid: str) -> dict:
    m = load_manifest(pid)
    root = {"name": "", "type": "dir", "children": {}}
    for rel, info in sorted(m["files"].items()):
        parts = rel.split("/")
        node = root
        for i, part in enumerate(parts):
            leaf = i == len(parts) - 1
            if leaf:
                node["children"][part] = {"name": part, "type": "file",
                                          "role": info.get("role"), "summary": info.get("summary")}
            else:
                node = node["children"].setdefault(
                    part, {"name": part, "type": "dir", "children": {}})

    def to_list(n):
        if n["type"] == "file":
            return n
        return {"name": n["name"], "type": "dir",
                "children": [to_list(c) for c in n["children"].values()]}

    # role distribution for a quick overview
    roles = {}
    for info in m["files"].values():
        roles[info.get("role", "other")] = roles.get(info.get("role", "other"), 0) + 1

    return {
        "tree": [to_list(c) for c in root["children"].values()],
        "readme": m.get("readme", ""),
        "file_count": len(m["files"]),
        "generated_at": m.get("generated_at"),
        "tokens": m.get("tokens", 0),
        "roles": sorted(roles.items(), key=lambda kv: -kv[1]),
    }
