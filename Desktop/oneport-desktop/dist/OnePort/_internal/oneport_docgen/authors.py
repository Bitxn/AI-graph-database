# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Author discovery — pre-fill the sign-off block so nobody types it by hand.

Enterprise documents always carry an author list; here we mine it from the repo
itself: top commit authors (via `git log`) and CODEOWNERS. Everything is best
effort — on a non-git tree or without CODEOWNERS we simply return what we found
(possibly nothing), and the CLI lets the user confirm or override.
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

_BOT = re.compile(r"(\[bot\]|dependabot|github-actions|renovate)", re.IGNORECASE)
_CODEOWNERS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


def git_authors(root: Path, limit: int = 5) -> list[str]:
    """Top human commit authors by commit count, most active first."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--no-merges", "--format=%an"],
            capture_output=True, text=True, timeout=15,
            # Git emits UTF-8; without this, Windows decodes as cp1252 and crashes
            # the reader thread on non-ASCII author names (e.g. "Armin Rönacher").
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    counts: Counter[str] = Counter()
    for name in (out.stdout or "").splitlines():
        name = name.strip()
        if name and not _BOT.search(name):
            counts[name] += 1
    return [name for name, _ in counts.most_common(limit)]


def codeowners(root: Path, limit: int = 5) -> list[str]:
    """Owner handles listed in a CODEOWNERS file (deduped, order preserved)."""
    for rel in _CODEOWNERS:
        p = root / rel
        if not p.exists():
            continue
        owners: list[str] = []
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for tok in line.split()[1:]:
                if tok.startswith("@") and tok not in owners:
                    owners.append(tok)
        if owners:
            return owners[:limit]
    return []


def suggest_authors(root: Path, limit: int = 3) -> list[str]:
    """Best available author list: git history first, else CODEOWNERS."""
    return git_authors(root, limit) or codeowners(root, limit)
