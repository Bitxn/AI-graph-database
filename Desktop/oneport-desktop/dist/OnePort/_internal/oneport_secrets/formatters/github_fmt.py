"""
GitHub markdown for `--post`: a sticky report comment (starts with the hidden
marker so it upserts in place) and inline review comments anchored to changed
lines. Secrets are always shown redacted.
"""

from __future__ import annotations

from oneport_secrets.markers import SECRETS_MARKER
from oneport_secrets.result import Finding, ScanResult, Verdict

_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}


def build_comment(result: ScanResult) -> str:
    """The sticky summary comment body (markdown)."""
    if not result.blocking:
        fp = len(result.false_positives)
        note = f" ({fp} placeholder/fixture match(es) triaged away)" if fp else ""
        return (
            f"{SECRETS_MARKER}\n## ✅ Oneport Secrets\n\n"
            f"No real secrets found in the {result.mode} scan{note}."
        )

    lines = [SECRETS_MARKER, "## 🔴 Oneport Secrets — action required", ""]
    lines.append(f"**{len(result.blocking)} secret(s)** must be revoked before this ships.\n")
    lines.append("| Severity | Secret | Location | Verdict |")
    lines.append("|----------|--------|----------|---------|")
    for f in result.blocking:
        v = "real" if f.verdict == Verdict.REAL else "unreviewed"
        loc = f.location.replace("|", "\\|")
        lines.append(
            f"| {_EMOJI.get(f.severity, '⚪')} {f.severity} | `{f.detector_name}` "
            f"`{f.redacted()}` | `{loc}` | {v} |"
        )
    lines.append("")
    for f in result.blocking:
        lines.append(f"### {_EMOJI.get(f.severity, '⚪')} {f.detector_name} — `{f.location}`")
        if f.reason:
            lines.append(f"> {f.reason}")
        if f.remediation:
            lines.append("")
            for step in f.remediation:
                lines.append(f"1. {step}")
        lines.append("")
    lines.append("---\n*Scanned by [Oneport Secrets](https://oneport.dev) · "
                 f"model: `{result.model or 'deterministic-only'}`*")
    return "\n".join(lines)


def build_inline_comments(result: ScanResult, commentable: dict[str, set[int]]) -> list[dict]:
    """Inline review comments for findings whose (path, line) is in the PR diff."""
    comments: list[dict] = []
    for f in result.blocking:
        if f.line in commentable.get(f.path, set()):
            comments.append({"path": f.path, "line": f.line, "body": _inline_body(f)})
    return comments


def _inline_body(f: Finding) -> str:
    parts = [
        f"{_EMOJI.get(f.severity, '⚪')} **{f.detector_name}** — possible secret (`{f.redacted()}`)",
    ]
    if f.reason:
        parts.append(f"\n{f.reason}")
    if f.remediation:
        parts.append("\n**Remediate:**")
        parts += [f"- {s}" for s in f.remediation[:3]]
    return "\n".join(parts)


def commentable_lines(diff_text: str) -> dict[str, set[int]]:
    """Map file path → set of new-file line numbers that appear in the diff."""
    from oneport_secrets.scanner import iter_added_lines
    out: dict[str, set[int]] = {}
    for path, lineno, _ in iter_added_lines(diff_text):
        out.setdefault(path, set()).add(lineno)
    return out
