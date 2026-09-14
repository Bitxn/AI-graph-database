"""
Grounding — enforce the narrator's "don't invent tickets" rule instead of hoping.

The narrator is told, in its prompt, to only use what the commits state and never
invent ticket or PR references. That's a good instruction, but an instruction is
not a guarantee: a model will occasionally emit a bullet citing `ABC-999` or
`#500` that appears in no commit. A status update that fabricates a reference is
exactly the kind of confident-but-wrong output OnePort refuses to ship.

So after the model returns, we check every ticket/PR reference in the narrative
against the references that actually appear in the commits. Any reference that is
in *no* commit is stripped from the text, and the report notes that it happened.
The regexes are the same ones the collector uses to pull refs off commit
subjects, so "what counts as a reference" can't drift between the two.
"""
from __future__ import annotations

import re

from oneport_standup.collector import _PR_RE, _TICKET_RE
from oneport_standup.result import Commit, Narrative


def _refs_in(text: str) -> list[str]:
    """Every ticket (ABC-123) and PR (#42) reference in a string."""
    return _TICKET_RE.findall(text or "") + _PR_RE.findall(text or "")


def _allowed_refs(commits: list[Commit]) -> set[str]:
    allowed: set[str] = set()
    for c in commits:
        allowed.update(c.tickets)
        allowed.update(c.prs)
    return allowed


def _strip_ref(text: str, ref: str) -> str:
    """Remove one fabricated reference and tidy the surrounding punctuation.

    Handles the common shapes a model produces — `foo (ABC-999)`, `foo [ABC-999]`,
    `foo ABC-999` — then collapses the empty brackets / doubled spaces / stranded
    punctuation left behind, so the bullet still reads naturally."""
    escaped = re.escape(ref)
    text = re.sub(r"\s*[\(\[]\s*" + escaped + r"\s*[\)\]]", "", text)  # (ABC-999) / [ABC-999]
    text = re.sub(r"\s*" + escaped, "", text)                          # bare ABC-999
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)                        # empty leftovers
    text = re.sub(r"\s+([,.;:])", r"\1", text)                         # " ," -> ","
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def ground_narrative(narrative: Narrative, commits: list[Commit]) -> int:
    """Strip references the narrative invented (not present in any commit).

    Mutates the narrative in place and returns the number of fabricated
    references removed (0 when the narrative was fully grounded)."""
    allowed = _allowed_refs(commits)
    removed = 0

    def clean(text: str) -> str:
        nonlocal removed
        for ref in _refs_in(text):
            if ref not in allowed:
                text = _strip_ref(text, ref)
                removed += 1
        return text

    narrative.headline = clean(narrative.headline)
    for group in narrative.groups:
        group.bullets = [clean(b) for b in group.bullets]
        group.bullets = [b for b in group.bullets if b]   # drop any now-empty bullet
    return removed
