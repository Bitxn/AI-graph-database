"""
Evidence grounding — the honesty layer.

An LLM asked for a TIMELINE will happily produce clean `[HH:MM]` events even
when the logs it was given had no timestamps at all. A post-mortem that invents
its own timeline is worse than none: it reads authoritative and is wrong. So
after generation we check every timestamp the model put in the timeline against
the timestamps that actually appear in the source (logs + Slack). Any timeline
time we can't find in the evidence is flagged `⚠ unverified` — shown, never
silently trusted.

This is a heuristic, and we label it as one. A match means "a log line with
this minute exists", not "this exact event happened". A miss means the model
either inferred the time or made it up — either way the reader should check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# HH:MM or HH:MM:SS, 24-hour, optionally bracketed. Captures hours+minutes.
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?\b")
# A generated timeline line: leading "[HH:MM] ..." (seconds optional).
_TIMELINE_LINE = re.compile(r"^\s*\[?\s*([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?\s*\]?\s*(.*)$")


def _minutes_in(text: str) -> set[str]:
    """All HH:MM stamps appearing anywhere in ``text``, normalised to zero-padded."""
    out: set[str] = set()
    for h, m in _TIME.findall(text or ""):
        out.add(f"{int(h):02d}:{m}")
    return out


@dataclass
class TimelineClaim:
    time: str          # normalised HH:MM
    event: str
    verified: bool     # the same minute appears in the source


@dataclass
class GroundingReport:
    claims: list[TimelineClaim]
    source_had_timestamps: bool

    @property
    def unverified(self) -> list[TimelineClaim]:
        return [c for c in self.claims if not c.verified]

    @property
    def checked(self) -> int:
        return len(self.claims)

    def note(self) -> str:
        """One-line verdict for the CLI, or '' when there's nothing to say."""
        if not self.claims:
            return ""
        if not self.source_had_timestamps:
            return (f"{self.checked} timeline entr{'y' if self.checked == 1 else 'ies'} could not be "
                    "verified — the source contained no timestamps, so all times are inferred.")
        n = len(self.unverified)
        if n == 0:
            return f"All {self.checked} timeline timestamps matched the source logs."
        return (f"{n} of {self.checked} timeline timestamp(s) were NOT found in the source "
                "— they may be inferred or fabricated. Verify before relying on them.")


def verify_timeline(postmortem_text: str, source_text: str) -> GroundingReport:
    """Cross-check the generated TIMELINE section against the source evidence."""
    source_minutes = _minutes_in(source_text)
    source_had = bool(source_minutes)

    claims: list[TimelineClaim] = []
    in_timeline = False
    for raw in (postmortem_text or "").splitlines():
        line = raw.rstrip()
        upper = line.strip().upper()
        if upper.startswith("TIMELINE"):
            in_timeline = True
            continue
        if in_timeline:
            # Any other ALL-CAPS section header ends the timeline block.
            if re.match(r"^[A-Z][A-Z0-9 /()\-]{3,}:?$", line.strip()) and not _TIMELINE_LINE.match(line):
                break
            m = _TIMELINE_LINE.match(line)
            if not m:
                continue
            h, mn, event = m.group(1), m.group(2), m.group(3).strip()
            stamp = f"{int(h):02d}:{mn}"
            claims.append(TimelineClaim(
                time=stamp,
                event=event,
                verified=stamp in source_minutes,
            ))

    return GroundingReport(claims=claims, source_had_timestamps=source_had)


def annotate(postmortem_text: str, report: GroundingReport) -> str:
    """Append a `⚠ unverified` marker to timeline lines whose time isn't in source.

    Leaves a fully-verified (or empty) timeline untouched so clean output stays
    clean. Operates line-by-line so it never reorders or drops content."""
    if not report.unverified:
        return postmortem_text

    unverified_times = {c.time for c in report.unverified}
    out_lines: list[str] = []
    in_timeline = False
    for raw in postmortem_text.splitlines():
        line = raw.rstrip()
        upper = line.strip().upper()
        if upper.startswith("TIMELINE"):
            in_timeline = True
            out_lines.append(raw)
            continue
        if in_timeline:
            if re.match(r"^[A-Z][A-Z0-9 /()\-]{3,}:?$", line.strip()) and not _TIMELINE_LINE.match(line):
                in_timeline = False
            else:
                m = _TIMELINE_LINE.match(line)
                if m:
                    stamp = f"{int(m.group(1)):02d}:{m.group(2)}"
                    if stamp in unverified_times and "⚠ unverified" not in line:
                        out_lines.append(raw + "   ⚠ unverified — not found in source logs")
                        continue
        out_lines.append(raw)
    return "\n".join(out_lines)
