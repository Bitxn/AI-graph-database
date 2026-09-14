"""
fixes.py — AI remediation bundles for blocking findings.

When a project's gates block, this produces a concrete fix for each issue: the
root cause and the exact change to make, grounded in the gate results, the diff,
and the affected files' summaries. It never invents a fix it can't justify from
that context — if it can't tell, it says what's missing.
"""
from __future__ import annotations

import chat
import repomap

DEFAULT_MODEL = "gemini-2.5-flash"

SYSTEM = (
    "You are OnePort's remediation assistant. You are given the blocking/errored "
    "security-gate results for a code change, the current diff, and summaries of "
    "the relevant files. For EACH blocking or errored issue, produce a concrete "
    "fix:\n"
    "- a one-line root cause,\n"
    "- the exact change to make — a minimal code snippet or clear step-by-step, "
    "citing the specific file,\n"
    "- if a gate ERRORED (tool failure, not a finding), explain the likely cause "
    "and how to unblock it.\n"
    "Ground ONLY in the provided context. If a fix can't be determined from what "
    "is given, say exactly what extra information is needed — never invent code or "
    "findings. Output Markdown, one '## ' heading per issue. Be concrete and minimal."
)


def generate(pid: str, root: str, project: dict, manifest: dict,
             model: str = DEFAULT_MODEL, gemini_key: str | None = None) -> dict:
    hist = project.get("history", []) or []
    latest = hist[0] if hist else None
    if not latest:
        return {"error": "Run a scan first — there's nothing to fix yet."}
    issues = [g for g in latest.get("gates", [])
              if g.get("status") in ("block", "error")]
    if not issues:
        return {"error": "No blocking or errored gates — nothing to fix. 🎉"}

    diff = chat._current_diff(root)
    files = manifest.get("files", {})
    fsum = [f"- {r} [{files[r].get('role')}] {files[r].get('summary')}"
            for r in chat._changed_files(diff) if r in files][:15]

    parts = ["BLOCKING / ERRORED GATES:\n" + "\n".join(
        f"- {g.get('title')} [{g.get('status')}]: {g.get('summary')}" for g in issues)]
    if fsum:
        parts.append("RELEVANT FILES:\n" + "\n".join(fsum))
    parts.append("CURRENT DIFF:\n" + (f"```diff\n{diff}\n```" if diff
                 else "(no uncommitted diff — reason from the gate results)"))
    user = "\n\n".join(parts) + "\n\nProduce the fix bundle now."

    try:
        text, tokens = repomap._llm(SYSTEM, user, model, gemini_key,
                                    max_tokens=1600, json_mode=False)
    except Exception as exc:
        return {"error": f"Fix generation failed: {exc}"}

    return {"markdown": text.strip(), "tokens": tokens, "issues": len(issues)}
