"""
diagnose.py — AI troubleshooting. Turns a cryptic error into plain guidance.

Whenever something fails — a gate errors, the auto-fix agent can't sign in, a
tool is misconfigured — OnePort hands the AI exactly what the user was doing and
the raw error, and gets back a short, Windows-specific, numbered set of steps to
fix it. The developer never has to decode a stack trace alone.
"""
from __future__ import annotations

import repomap

SYSTEM = (
    "You are OnePort's troubleshooting assistant for a developer on Windows. You "
    "are given what the user was doing and the exact error text. Respond with:\n"
    "1. One plain-language sentence on what went wrong.\n"
    "2. A short numbered list of SPECIFIC, copy-pasteable steps to fix it — the "
    "simplest reliable fix first, exact commands where relevant.\n"
    "If it's an auth/login problem with a CLI (e.g. Claude Code / `claude`), give "
    "the exact command(s) and where to run them (a real terminal, not an in-app "
    "one). Be concise and concrete. Short Markdown, no preamble."
)


def explain(what: str, error_text: str, model: str = "gemini-2.5-flash",
            gemini_key: str | None = None) -> dict:
    user = (f"WHAT THE USER WAS DOING: {what}\n\n"
            f"EXACT ERROR / OUTPUT:\n{(error_text or '(no output)')[:2500]}\n\n"
            "Explain and give the fix steps.")
    try:
        text, tokens = repomap._llm(SYSTEM, user, model, gemini_key,
                                    max_tokens=700, json_mode=False)
    except Exception as exc:
        return {"error": f"Diagnosis unavailable: {exc}"}
    return {"guidance": text.strip(), "tokens": tokens}
