# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Index freshness — never present a stale understanding as current.

The index is cached to `.context/index.json` so `show` / `video` / `pdf` / chat
are instant. But a cached index is a photograph: the moment code changes, the
tour it drives can be quietly wrong — a new engineer would never know the map is
out of date. This module compares a cached index against the repo as it is right
now (a cheap, model-free re-hash of the source files) and reports exactly what
drifted, so the caller can warn or refresh instead of silently misleading.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from oneport_context.models import RepoIndex
from oneport_context.scan import ScanConfig
from oneport_context.walker import scan_signatures


@dataclass
class FreshnessReport:
    added: list[str] = field(default_factory=list)     # files present now, not in index
    removed: list[str] = field(default_factory=list)   # files in index, gone now
    modified: list[str] = field(default_factory=list)  # same path, changed contents

    @property
    def is_stale(self) -> bool:
        return bool(self.added or self.removed or self.modified)

    @property
    def change_count(self) -> int:
        return len(self.added) + len(self.removed) + len(self.modified)

    def summary(self) -> str:
        """A one-line, human description of the drift (empty when fresh)."""
        if not self.is_stale:
            return "index is up to date"
        bits = []
        if self.added:
            bits.append(f"{len(self.added)} added")
        if self.removed:
            bits.append(f"{len(self.removed)} removed")
        if self.modified:
            bits.append(f"{len(self.modified)} modified")
        return "index is stale — " + ", ".join(bits) + " since it was built"


def check_freshness(index: RepoIndex, root: Path,
                    scan: ScanConfig | None = None) -> FreshnessReport:
    """Diff a cached ``index`` against the current state of ``root``.

    Model-free and cheap (it re-hashes source files, nothing more). If the index
    predates content hashing (older index.json with no hashes), every file reads
    as 'modified' rather than silently claiming freshness — the honest default."""
    current = scan_signatures(root, scan)
    baseline = index.signatures()

    cur_paths, base_paths = set(current), set(baseline)
    added = sorted(cur_paths - base_paths)
    removed = sorted(base_paths - cur_paths)
    modified = sorted(
        p for p in (cur_paths & base_paths)
        if current[p] != baseline[p]
    )
    return FreshnessReport(added=added, removed=removed, modified=modified)
