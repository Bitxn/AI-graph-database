"""Orchestration: collect commits (facts) → narrate (judgment) → StandupReport."""

from __future__ import annotations

import time
from pathlib import Path

from oneport_standup.collector import collect_commits
from oneport_standup.config import Config
from oneport_standup.exceptions import NothingToReport
from oneport_standup.guidelines import load_guidelines
from oneport_standup.narrator import narrate
from oneport_standup.ranges import Author, Window
from oneport_standup.result import StandupReport


def build_report(
    config: Config, root: str | Path, mode: str, window: Window, author: Author,
    use_llm: bool = True,
) -> StandupReport:
    start = time.monotonic()
    commits = collect_commits(
        root, revrange=window.revrange, since=window.since, author_email=author.email)

    if not commits:
        raise NothingToReport(
            f"No commits {window.label} for {author.label}. "
            "Nothing to report — go write some code first. 🙂")

    report = StandupReport(
        mode=mode, range_label=window.label, author_label=author.label,
        commits=commits, model=config.model)

    if use_llm and config.has_key:   # has_key == logged in to Oneport
        narrative, tokens = narrate(commits, mode, config, load_guidelines(config.guidelines_path))
        # Enforce the narrator's "don't invent tickets" rule: strip any ticket/PR
        # reference the model produced that isn't in a real commit.
        from oneport_standup.grounding import ground_narrative
        invented = ground_narrative(narrative, commits)
        report.narrative = narrative
        report.total_tokens = tokens
        if invented:
            report.notes.append(
                f"Removed {invented} fabricated reference(s) the summary invented "
                "(not present in any commit)."
            )
        if narrative.is_empty:
            report.notes.append(
                "Narration unavailable (model busy or rate-limited) — showing commits "
                "grouped by day. Try again in a minute."
            )
    elif use_llm and not config.has_key:
        report.notes.append("Not logged in to Oneport — showing commits grouped by day. "
                            "Run `oneport-account login <token>` for a written summary.")

    report.elapsed_ms = int((time.monotonic() - start) * 1000)
    return report
