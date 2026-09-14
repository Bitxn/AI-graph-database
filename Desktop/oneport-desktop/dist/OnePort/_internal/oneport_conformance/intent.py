"""
The intent document — how the product team says the software *should* be.

Plain markdown the team writes and commits (default `.oneport/intent.md`):

    # Payments
    - All money movement must go through the ledger service.
    - Never call the Stripe SDK directly from a route handler.

    # Auth
    - Access tokens expire in 24h; refresh tokens in 30d.

Each bullet (or, if there are no bullets, each non-heading line) becomes a
numbered rule the checker can cite by id, so every reported deviation points
back at a specific line the team wrote — never a vibe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oneport_conformance.exceptions import IntentError

DEFAULT_INTENT_PATHS = (
    ".oneport/intent.md",
    ".oneport/intent.txt",
    "ONEPORT_INTENT.md",
)


@dataclass(frozen=True)
class Rule:
    id: str          # R1, R2, …
    text: str        # the line the team wrote
    section: str     # nearest preceding heading, for context


@dataclass(frozen=True)
class Intent:
    raw: str
    rules: list[Rule]

    def enumerated(self) -> str:
        """Rules as a numbered block for the prompt (with section context)."""
        return "\n".join(
            f"{r.id} [{r.section}] {r.text}" if r.section else f"{r.id} {r.text}"
            for r in self.rules
        )


def find_intent(repo: str | Path | None = None) -> Path | None:
    root = Path(repo or Path.cwd())
    for rel in DEFAULT_INTENT_PATHS:
        p = root / rel
        if p.is_file():
            return p
    return None


def load_intent(path: str | Path) -> Intent:
    p = Path(path)
    if not p.is_file():
        raise IntentError(
            f"Intent doc not found: {p}. Write one at .oneport/intent.md "
            "describing how the software should behave, then re-run."
        )
    try:
        raw = p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        raise IntentError(f"Could not read intent doc {p}: {exc}") from exc

    if not raw:
        raise IntentError(
            f"Intent doc {p} is empty. Conformance can't be judged against "
            "nothing — describe the intended behaviour first."
        )

    rules = _parse_rules(raw)
    if not rules:
        raise IntentError(
            f"Intent doc {p} has no checkable statements. Add bullet points "
            "describing how the software should behave."
        )
    return Intent(raw=raw, rules=rules)


def _parse_rules(raw: str) -> list[Rule]:
    rules: list[Rule] = []
    section = ""
    n = 0
    # First pass: prefer list items (the natural unit of a rule).
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            section = s.lstrip("#").strip()
            continue
        text = _strip_list_marker(s)
        if text is not None and text:
            n += 1
            rules.append(Rule(id=f"R{n}", text=text, section=section))

    if rules:
        return rules

    # No bullets: treat each non-heading line as a rule.
    section = ""
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            section = s.lstrip("#").strip()
            continue
        n += 1
        rules.append(Rule(id=f"R{n}", text=s, section=section))
    return rules


def _strip_list_marker(s: str) -> str | None:
    """Return the text of a bullet/numbered list item, or None if not a list item."""
    if s[:1] in ("-", "*", "•"):
        return s[1:].strip()
    # numbered: "1." / "2)" / "10 -"
    i = 0
    while i < len(s) and s[i].isdigit():
        i += 1
    if i > 0 and i < len(s) and s[i] in ").-":
        return s[i + 1:].strip()
    return None
