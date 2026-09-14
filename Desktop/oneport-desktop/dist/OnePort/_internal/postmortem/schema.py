"""
Structured post-mortem model + parser.

The model emits a fixed text layout (TITLE:/TIMELINE:/ROOT CAUSE.../ACTION ITEMS
...). Re-scraping that text with ad-hoc regex at every call site is fragile and
was duplicated between the DB layer and the exporter. This module parses the
layout ONCE into a typed object, so:

  * `--format json` can emit a machine-readable post-mortem for other systems,
  * the local pattern DB is populated from structured fields, not re-scraped,
  * grounding annotations travel alongside the data.

Parsing is intentionally forgiving: a missing section yields an empty value,
never an exception. The raw text is always preserved so nothing is lost.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

_HEADER_FIELDS = ("TITLE", "DATE", "DURATION", "SEVERITY", "IMPACT")
_SECTIONS = ("SUMMARY", "TIMELINE", "ROOT CAUSE", "CONTRIBUTING", "ACTION ITEMS",
             "LESSONS", "EVIDENCE")


@dataclass
class TimelineEntry:
    time: str
    event: str
    verified: bool | None = None   # set by grounding; None = not checked


@dataclass
class ActionItem:
    priority: str
    description: str
    owner: str = ""
    due_date: str = ""


@dataclass
class PostMortem:
    title: str = ""
    date: str = ""
    severity: str = ""
    duration: str = ""
    impact: str = ""
    summary: str = ""
    timeline: list[TimelineEntry] = field(default_factory=list)
    whys: list[str] = field(default_factory=list)        # the 5-Whys chain, in order
    root_cause: str = ""                                  # final "why" — the true cause
    contributing_factors: list[str] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    lessons: str = ""
    evidence: list[str] = field(default_factory=list)
    raw_text: str = ""
    unverified_timeline: int = 0                          # count flagged by grounding

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ── field / section extraction (shared with database.py) ────────────────────

def extract_field(raw_text: str, field_name: str) -> str:
    """Single-line header field, e.g. `TITLE: ...`."""
    m = re.search(rf"^{field_name}:\s*(.+)$", raw_text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _section_body(raw_text: str, name: str) -> str:
    """Text between a section header and the next known header."""
    others = "|".join(re.escape(s) for s in _SECTIONS if s != name)
    pattern = rf"{re.escape(name)}[^\n:]*:?\s*\n(.*?)(?=\n(?:{others}|{'|'.join(_HEADER_FIELDS)})\b|\Z)"
    m = re.search(pattern, raw_text, re.DOTALL)
    return m.group(1).strip() if m else ""


def extract_whys(raw_text: str) -> list[str]:
    """The ordered 5-Whys arrow lines from the ROOT CAUSE section."""
    body = _section_body(raw_text, "ROOT CAUSE")
    return [ln.strip() for ln in body.splitlines() if "→" in ln and ln.strip()]


def extract_root_cause(raw_text: str) -> str:
    """The final 'why' — text after the last arrow in the 5-Whys chain."""
    whys = extract_whys(raw_text)
    if not whys:
        return ""
    return whys[-1].split("→")[-1].strip()


def extract_timeline(raw_text: str) -> list[TimelineEntry]:
    body = _section_body(raw_text, "TIMELINE")
    entries: list[TimelineEntry] = []
    for ln in body.splitlines():
        m = re.match(r"\s*\[?\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\]?\s*(.*)$", ln)
        if m and m.group(2).strip():
            entries.append(TimelineEntry(time=m.group(1), event=m.group(2).strip()))
    return entries


def extract_action_items(raw_text: str) -> list[ActionItem]:
    body = _section_body(raw_text, "ACTION ITEMS")
    items: list[ActionItem] = []
    current: ActionItem | None = None
    for ln in body.splitlines():
        m = re.match(r"\[(P[123])\]\s+(.+)", ln.strip())
        if m:
            if current:
                items.append(current)
            current = ActionItem(priority=m.group(1), description=m.group(2).strip())
        elif current and ln.strip().startswith("Owner:"):
            owner = re.search(r"Owner:\s*([^·]+)", ln)
            due = re.search(r"Due:\s*(.+)$", ln)
            if owner:
                current.owner = owner.group(1).strip()
            if due:
                current.due_date = due.group(1).strip()
    if current:
        items.append(current)
    return items


def _extract_bullets(raw_text: str, name: str) -> list[str]:
    body = _section_body(raw_text, name)
    out = []
    for ln in body.splitlines():
        s = ln.strip().lstrip("-•*").strip()
        if s:
            out.append(s)
    return out


def parse_postmortem(raw_text: str) -> PostMortem:
    """Parse the model's text layout into a typed PostMortem (never raises)."""
    pm = PostMortem(raw_text=raw_text)
    pm.title = extract_field(raw_text, "TITLE")
    pm.date = extract_field(raw_text, "DATE")
    pm.severity = extract_field(raw_text, "SEVERITY")
    pm.duration = extract_field(raw_text, "DURATION")
    pm.impact = extract_field(raw_text, "IMPACT")
    pm.summary = _section_body(raw_text, "SUMMARY")
    pm.timeline = extract_timeline(raw_text)
    pm.whys = extract_whys(raw_text)
    pm.root_cause = extract_root_cause(raw_text)
    pm.contributing_factors = _extract_bullets(raw_text, "CONTRIBUTING")
    pm.action_items = extract_action_items(raw_text)
    pm.lessons = _section_body(raw_text, "LESSONS")
    pm.evidence = _extract_bullets(raw_text, "EVIDENCE")
    return pm
