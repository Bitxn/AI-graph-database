"""
The write-guard — blocks an AI agent from writing a secret into your code.

This is the hook side of oneport-mcp. Claude Code (and Cursor/Codex, with their
hook formats) calls `oneport-mcp guard` BEFORE an Edit/Write lands: the hook
receives the tool call as JSON on stdin, we scan the content that is ABOUT to be
written, and exit 2 to block it — with a reason on stderr that is fed back to
the model, so the agent self-corrects ("load it from an env var instead").

Design rules:
  * HIGH-CONFIDENCE patterns only. A blocking hook that cries wolf gets
    uninstalled in an hour. Formats like AKIA... or ghp_... are near-certain
    secrets; the generic `password = "..."` rule is deliberately conservative
    (long literal values only, placeholder-aware, env-read-aware).
  * FAIL OPEN. If stdin is malformed or anything throws, allow the edit
    (exit 0). This guard is the fast dev-time tripwire; the authoritative,
    fail-closed gate is `op ship` / oneport-secrets in CI. A guard bug must
    never brick someone's editing loop.
  * DETERMINISTIC and instant — pure regex, no network, no model, no login.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

# ── high-confidence secret formats ──────────────────────────────────────────
_FORMATS: list[tuple[str, re.Pattern]] = [
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[posur]_[A-Za-z0-9]{20,255}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Stripe live key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{16,}\b")),
    ("OpenAI key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}\b")),
]

# Generic `password = "<literal>"` style assignment. Conservative on purpose:
# quoted literal, 12+ chars, and filtered against placeholders below.
_ASSIGNMENT = re.compile(
    r"""(?i)\b(password|passwd|pwd|secret|api[_-]?key|apikey|token|auth
        |access[_-]?key|client[_-]?secret|private[_-]?key)\b
        \s*[=:]\s*["']([^"']{12,})["']""",
    re.VERBOSE,
)

# A value containing any of these is a placeholder/template, not a secret.
_PLACEHOLDER_MARKS = (
    "example", "placeholder", "your", "changeme", "change-me", "change_me",
    "dummy", "sample", "redacted", "fixme", "todo", "xxxx", "<", ">", "${",
    "{{", "%s", "...",
)
# A line that READS the secret from the environment is the correct pattern —
# never block it, whatever else it matches.
_ENV_READ = re.compile(r"getenv|environ|process\.env|secrets\.|vault|os\.env", re.IGNORECASE)


@dataclass
class Hit:
    kind: str
    line_no: int
    line: str


def _is_placeholder(value: str) -> bool:
    low = value.lower()
    return any(m in low for m in _PLACEHOLDER_MARKS)


def scan_text(text: str) -> list[Hit]:
    """Scan content for secrets about to be written. Returns high-confidence hits."""
    hits: list[Hit] = []
    for no, line in enumerate((text or "").splitlines(), start=1):
        if _ENV_READ.search(line):
            continue
        for kind, pat in _FORMATS:
            if pat.search(line):
                hits.append(Hit(kind, no, line.strip()[:120]))
                break
        else:
            m = _ASSIGNMENT.search(line)
            if m and not _is_placeholder(m.group(2)):
                hits.append(Hit(f"hardcoded {m.group(1).lower()}", no, line.strip()[:120]))
    return hits


def _texts_from_tool_input(tool_input: dict) -> str:
    """Pull the content that is about to be written, across Write/Edit/MultiEdit."""
    if not isinstance(tool_input, dict):
        return ""
    if isinstance(tool_input.get("content"), str):          # Write
        return tool_input["content"]
    if isinstance(tool_input.get("new_string"), str):       # Edit
        return tool_input["new_string"]
    edits = tool_input.get("edits")                          # MultiEdit
    if isinstance(edits, list):
        return "\n".join(e.get("new_string", "") for e in edits if isinstance(e, dict))
    return ""


def run_guard(stdin_text: str) -> int:
    """The hook entry: read the tool call, scan, exit 0 (allow) or 2 (block)."""
    try:
        payload = json.loads(stdin_text or "{}")
        tool_input = payload.get("tool_input", {})
        text = _texts_from_tool_input(tool_input)
        if not text:
            return 0
        hits = scan_text(text)
        if not hits:
            return 0
        path = tool_input.get("file_path", "the file")
        lines = "\n".join(f"  - {h.kind} (line {h.line_no}): {h.line}" for h in hits[:5])
        sys.stderr.write(
            f"OnePort blocked this edit: it would write a secret into {path}.\n"
            f"{lines}\n"
            "Do not hardcode credentials. Load them from an environment variable "
            "(os.getenv / process.env) or a secrets manager, and use an obvious "
            "placeholder like 'YOUR_API_KEY' in examples.\n"
        )
        return 2
    except Exception:
        # Fail open — the guard must never brick the user's editing loop.
        return 0
