"""
Ownership — "who do I ask about this?"

Two deterministic sources, no model:
  * git history for the path  → the people who actually wrote and maintain it,
    ranked by number of commits, with the date they last touched it;
  * CODEOWNERS                → the declared owners (teams / handles), matched by
    the same glob rules GitHub uses.

Declared owners come first (they're accountable); the git authors follow (they
have the context). Both are shown because they're often different people.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from oneport_impact.exceptions import GitError
from oneport_impact.integrations.local_git import run_git
from oneport_impact.result import Owner

_CODEOWNERS_LOCATIONS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


def git_authors(root: str | Path, path: str, top: int = 3) -> list[Owner]:
    """Top authors of `path` by commit count, most recent activity noted."""
    try:
        out = run_git(root, "log", "--follow", "--format=%an\x1f%aI", "--", path, timeout=30)
    except GitError:
        return []
    commits: dict[str, list[str]] = {}
    for line in out.splitlines():
        if "\x1f" not in line:
            continue
        name, date = line.split("\x1f", 1)
        commits.setdefault(name.strip(), []).append(date.strip())
    owners = [
        Owner(name=name, commits=len(dates), last_date=(max(dates)[:10] if dates else ""))
        for name, dates in commits.items()
    ]
    owners.sort(key=lambda o: (-o.commits, o.name))
    return owners[:top]


def load_codeowners(root: str | Path) -> list[tuple[str, list[str]]]:
    """Parse CODEOWNERS → ordered (glob, owners) rules (last match wins, GitHub-style)."""
    root = Path(root)
    rules: list[tuple[str, list[str]]] = []
    for rel in _CODEOWNERS_LOCATIONS:
        f = root / rel
        if not f.is_file():
            continue
        for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                rules.append((parts[0], parts[1:]))
        break  # first CODEOWNERS found wins
    return rules


def codeowners_for(path: str, rules: list[tuple[str, list[str]]]) -> list[str]:
    """Owners for `path` under GitHub's 'last matching pattern wins' semantics."""
    path = "/" + path.replace("\\", "/").lstrip("/")
    matched: list[str] = []
    for pattern, owners in rules:
        pat = pattern if pattern.startswith("/") else "/**/" + pattern
        pat = pat.replace("\\", "/")
        candidates = {pat, pat.rstrip("/") + "/**", "/**/" + pattern.lstrip("/")}
        if any(fnmatch.fnmatch(path, c) or fnmatch.fnmatch(path, c + "/**") for c in candidates):
            matched = owners
    return matched


def owners_of(root: str | Path, path: str, top: int = 3) -> list[Owner]:
    """Declared CODEOWNERS (first) + top git authors (after), deduped by handle."""
    rules = load_codeowners(root)
    declared = codeowners_for(path, rules)
    result: list[Owner] = [
        Owner(name=handle, commits=0, last_date="", codeowner=True) for handle in declared
    ]
    seen = {h.lstrip("@").lower() for h in declared}
    for author in git_authors(root, path, top=top):
        if author.name.lower() not in seen:
            result.append(author)
    return result
