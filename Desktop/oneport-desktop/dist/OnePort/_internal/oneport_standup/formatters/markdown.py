"""
Markdown output — the default, because the whole point is to paste it into Slack,
a standup doc, a PR description, or release notes.

When the narrative is present it drives the layout; otherwise we fall back to a
deterministic day-grouped commit list, so there is always something to paste.
"""

from __future__ import annotations

from oneport_standup.result import StandupReport

_TITLE = {
    "daily": "Standup", "weekly": "Weekly summary",
    "since": "Progress", "release": "Release notes",
}


def format_markdown(report: StandupReport) -> str:
    lines: list[str] = []
    title = _TITLE.get(report.mode, "Summary")
    lines.append(f"# {title} — {report.range_label}")
    if report.author_label != "everyone":
        lines.append(f"_{report.author_label}_")
    lines.append("")

    nar = report.narrative
    if not nar.is_empty:
        if nar.headline:
            lines.append(f"**{nar.headline}**")
            lines.append("")
        for g in nar.groups:
            if g.title:
                lines.append(f"## {g.title}")
            for b in g.bullets:
                lines.append(f"- {b}")
            lines.append("")
    else:
        # Deterministic fallback: commits grouped by day.
        for day, commits in report.by_day().items():
            lines.append(f"### {day}")
            for c in commits:
                refs = " ".join(c.tickets + c.prs)
                refs = f"  ({refs})" if refs else ""
                lines.append(f"- {c.subject}{refs}")
            lines.append("")

    # Surface any notes (e.g. "narration unavailable — rate-limited", "not logged
    # in") so a plain fallback list is never silently unexplained.
    for note in report.notes:
        lines.append(f"> _{note}_")
    if report.notes:
        lines.append("")

    lines.append(_stats_line(report))
    return "\n".join(lines).rstrip() + "\n"


def _stats_line(report: StandupReport) -> str:
    ins, dele = report.net_lines
    parts = [f"{len(report.commits)} commit(s)", f"{report.files_touched} file change(s)",
             f"+{ins}/-{dele}"]
    if report.tickets:
        parts.append("refs: " + ", ".join(report.tickets))
    return "_" + " · ".join(parts) + "_"
