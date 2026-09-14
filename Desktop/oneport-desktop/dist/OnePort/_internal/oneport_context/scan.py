# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Scan configuration — what the walker looks at, made tunable and .gitignore-aware.

The walker's defaults (skip node_modules/.venv/…, cap at 4000 files) are sane
for most repos, but a real monorepo needs to say "also skip generated/, treat
.astro as a source file, and stop pretending my 12k-file repo is 4k". This
module holds that configuration, resolves the repo's own `.gitignore`, and
exposes a single "should I look at this path?" decision the walker calls.

Config lives in an optional `.oneport-context.yml` at the repo root:

    scan:
      ignore: ["generated/**", "*.pb.go"]   # extra glob patterns to skip
      languages: {".astro": "Astro"}        # extra extension -> language
      max_files: 12000
      max_file_bytes: 800000
      respect_gitignore: true               # default true
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# The built-in directory blocklist (moved here from walker so it's overridable).
DEFAULT_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "target", ".next", ".nuxt", "out", "vendor", ".idea", ".vscode",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "coverage", "htmlcov", ".context",
    "site-packages", ".tox", "bin", "obj",
}

DEFAULT_MAX_FILES = 4000
DEFAULT_MAX_FILE_BYTES = 400_000
CONFIG_FILENAME = ".oneport-context.yml"


# --------------------------------------------------------------------------- #
# .gitignore matching (pragmatic subset of the spec)                            #
# --------------------------------------------------------------------------- #

@dataclass
class _Pattern:
    regex: re.Pattern
    negated: bool
    dir_only: bool


def _compile_gitignore_line(line: str) -> _Pattern | None:
    """Compile one .gitignore line to a matcher, or None to skip it.

    Supports the common cases: comments, negation (!), anchored (/foo),
    directory-only (foo/), wildcards (* ? **). Not a full implementation —
    character classes and some edge cases fall back to a literal match, which
    is safe (it just ignores less, never more)."""
    raw = line.rstrip("\n")
    if not raw.strip() or raw.lstrip().startswith("#"):
        return None
    negated = raw.startswith("!")
    if negated:
        raw = raw[1:]
    raw = raw.strip()
    if not raw:
        return None

    dir_only = raw.endswith("/")
    if dir_only:
        raw = raw[:-1]

    anchored = raw.startswith("/")
    if anchored:
        raw = raw[1:]

    # Build a regex from the glob, segment-aware so `*` doesn't cross `/`.
    i, n = 0, len(raw)
    out = ["^" if anchored else r"(?:^|.*/)"]
    while i < n:
        c = raw[i]
        if c == "*":
            if i + 1 < n and raw[i + 1] == "*":
                out.append(".*")
                i += 2
                if i < n and raw[i] == "/":
                    i += 1
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    # A pattern with no trailing slash matches the path itself and everything
    # under it (gitignore treats `foo` as `foo` and `foo/**`).
    out.append(r"(?:/.*)?$")
    try:
        return _Pattern(re.compile("".join(out)), negated, dir_only)
    except re.error:
        return None


class GitignoreMatcher:
    """Matches repo-relative POSIX paths against a set of .gitignore patterns.

    Later patterns win (so a negation can re-include a previously ignored path),
    matching git's own precedence."""

    def __init__(self, patterns: list[_Pattern]):
        self._patterns = patterns

    @classmethod
    def from_text(cls, text: str) -> "GitignoreMatcher":
        pats = [p for p in (_compile_gitignore_line(ln) for ln in text.splitlines()) if p]
        return cls(pats)

    @classmethod
    def empty(cls) -> "GitignoreMatcher":
        return cls([])

    def is_ignored(self, rel_posix: str, is_dir: bool) -> bool:
        ignored = False
        for p in self._patterns:
            if p.dir_only and not is_dir:
                continue
            if p.regex.match(rel_posix):
                ignored = not p.negated
        return ignored


# --------------------------------------------------------------------------- #
# ScanConfig                                                                    #
# --------------------------------------------------------------------------- #

@dataclass
class ScanConfig:
    ignore_dirs: set[str] = field(default_factory=lambda: set(DEFAULT_IGNORE_DIRS))
    ignore_globs: list[str] = field(default_factory=list)      # extra path globs
    extra_extensions: dict[str, str] = field(default_factory=dict)  # ext -> language
    max_files: int = DEFAULT_MAX_FILES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    respect_gitignore: bool = True
    gitignore: GitignoreMatcher = field(default_factory=GitignoreMatcher.empty)
    # Set by the walker when the file cap was hit — surfaced so a truncated scan
    # is never silently presented as a complete one.
    truncated: bool = False

    def _glob_regexes(self) -> list[re.Pattern]:
        if not hasattr(self, "_glob_cache"):
            self._glob_cache = [
                p.regex for p in
                (_compile_gitignore_line(g) for g in self.ignore_globs) if p
            ]
        return self._glob_cache

    def is_ignored_dir(self, name: str, rel_posix: str) -> bool:
        if name in self.ignore_dirs or name.startswith("."):
            return True
        if self.respect_gitignore and self.gitignore.is_ignored(rel_posix, is_dir=True):
            return True
        return any(rx.match(rel_posix) for rx in self._glob_regexes())

    def is_ignored_file(self, rel_posix: str) -> bool:
        if self.respect_gitignore and self.gitignore.is_ignored(rel_posix, is_dir=False):
            return True
        return any(rx.match(rel_posix) for rx in self._glob_regexes())


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_scan_config(root: Path) -> ScanConfig:
    """Build a ScanConfig for ``root``: defaults + optional .oneport-context.yml
    + the repo's .gitignore (unless disabled)."""
    root = Path(root)
    cfg = ScanConfig()

    raw = _load_yaml(root / CONFIG_FILENAME).get("scan", {}) if (root / CONFIG_FILENAME).exists() else {}
    if isinstance(raw, dict):
        for name in raw.get("ignore_dirs", []) or []:
            cfg.ignore_dirs.add(str(name))
        cfg.ignore_globs = [str(g) for g in (raw.get("ignore", []) or [])]
        exts = raw.get("languages", {}) or {}
        if isinstance(exts, dict):
            cfg.extra_extensions = {str(k).lower(): str(v) for k, v in exts.items()}
        if isinstance(raw.get("max_files"), int):
            cfg.max_files = raw["max_files"]
        if isinstance(raw.get("max_file_bytes"), int):
            cfg.max_file_bytes = raw["max_file_bytes"]
        if isinstance(raw.get("respect_gitignore"), bool):
            cfg.respect_gitignore = raw["respect_gitignore"]

    if cfg.respect_gitignore:
        gi = root / ".gitignore"
        if gi.exists():
            try:
                cfg.gitignore = GitignoreMatcher.from_text(gi.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return cfg
