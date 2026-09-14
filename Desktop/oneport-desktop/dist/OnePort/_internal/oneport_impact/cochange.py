"""
Temporal coupling / co-change mining — the differentiated signal.

Two files that keep changing in the SAME commits are coupled *in practice*, even
when there is no import between them (a model and its serializer, an API route and
its client SDK, a config key and the code that reads it). Static analysis can never
see this; only history can. That is the gap this fills — and doing it locally, from
the repo's own `.git`, means nothing is uploaded to learn it.

Method (fully deterministic):
  * read up to `max_commits` recent non-merge commits and the files each touched;
  * a commit that touches an implausible number of files (mass reformat, vendor
    bump, license header sweep) is skipped — it couples everything to everything
    and is pure noise;
  * for a target file, confidence(partner) = commits(target ∧ partner) / commits(target).

No model is ever consulted. The LLM later *reads* these numbers; it never produces them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from oneport_impact.integrations.local_git import run_git
from oneport_impact.result import CoChangePartner

_COMMIT_MARK = "\x01"   # sentinel prefixing each SHA in the log stream


@dataclass
class CoChangeIndex:
    """Per-commit file sets, built once, queried per target."""
    commits: list[frozenset[str]] = field(default_factory=list)
    file_commits: Counter = field(default_factory=Counter)   # path -> #commits touched
    skipped_bulk: int = 0

    @property
    def n_commits(self) -> int:
        return len(self.commits)

    def target_commit_count(self, target: str) -> int:
        return self.file_commits.get(_norm(target), 0)

    def partners(
        self, target: str, min_together: int = 2, top: int = 12, min_confidence: float = 0.0,
    ) -> list[CoChangePartner]:
        target = _norm(target)
        total = self.file_commits.get(target, 0)
        if total == 0:
            return []
        together: Counter = Counter()
        for files in self.commits:
            if target in files:
                for other in files:
                    if other != target and not _is_frozen(other):
                        together[other] += 1
        out: list[CoChangePartner] = []
        for path, n in together.items():
            if n < min_together:
                continue
            conf = n / total
            if conf < min_confidence:
                continue
            out.append(CoChangePartner(path=path, together=n, target_commits=total, confidence=conf))
        out.sort(key=lambda p: (-p.together, -p.confidence, p.path))
        return out[:top]


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip()


def _is_frozen(path: str) -> bool:
    """Migrations/versions are frozen history — excluded from the call graph, so
    exclude them from co-change too. Otherwise the report says "5 migrations
    excluded" yet lists one under temporal coupling: an accurate-but-inconsistent
    result. A dead migration you'll never edit isn't actionable blast radius."""
    from oneport_impact.callgraph import _FROZEN_DIRS
    return any(part in _FROZEN_DIRS for part in _norm(path).split("/"))


def build_cochange_index(
    root: str | Path, max_commits: int = 1500, max_files_per_commit: int = 60,
) -> CoChangeIndex:
    """
    Build a CoChangeIndex from `git log`. `max_files_per_commit` drops sweeping
    commits that would couple unrelated files (they add noise, not signal).
    """
    # One SHA-marked stream: a sentinel+SHA line per commit, then its file paths.
    out = run_git(
        root, "log", "--no-merges", f"--max-count={max_commits}",
        "--name-only", f"--format={_COMMIT_MARK}%H", timeout=60,
    )

    index = CoChangeIndex()
    current: set[str] = set()
    have_commit = False

    def flush() -> None:
        nonlocal current
        if have_commit and current:
            if len(current) > max_files_per_commit:
                index.skipped_bulk += 1
            else:
                frozen = frozenset(current)
                index.commits.append(frozen)
                for f in frozen:
                    index.file_commits[f] += 1
        current = set()

    for line in out.splitlines():
        if line.startswith(_COMMIT_MARK):
            flush()
            have_commit = True
        elif line.strip():
            current.add(_norm(line))
    flush()

    return index
