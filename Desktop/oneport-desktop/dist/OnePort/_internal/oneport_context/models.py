# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
The RepoIndex — the single hierarchical understanding of a codebase.

Everything downstream reads from this: the walkthrough, the handbook, editor
hover, and chat. It's built bottom-up (file → module → repo) and cached to
`.context/index.json` so re-runs are instant and hover is free.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

INDEX_VERSION = 1


@dataclass
class Symbol:
    name: str
    kind: str            # function | class | method | const
    line: int


@dataclass
class FileNode:
    path: str            # repo-relative, forward slashes
    language: str
    loc: int
    summary: str = ""              # one line: what this file is for
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)   # raw import targets
    content_hash: str = ""         # short digest of the file bytes at index time;
                                   # powers staleness detection (see freshness.py)


@dataclass
class ModuleNode:
    name: str            # e.g. "services/billing" or top-level package
    summary: str = ""
    files: list[FileNode] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)  # other module names

    @property
    def loc(self) -> int:
        return sum(f.loc for f in self.files)


@dataclass
class RepoIndex:
    name: str
    root: str
    summary: str = ""            # elevator: what the whole repo is
    modules: list[ModuleNode] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)   # likely main files
    languages: dict[str, int] = field(default_factory=dict)  # lang -> file count
    file_count: int = 0
    total_loc: int = 0
    model_used: str = "heuristic"
    version: int = INDEX_VERSION
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # Digest of the scanned file set at index time, and whether the file cap was
    # hit. Both let downstream commands be honest about what the index reflects.
    fingerprint: str = ""
    truncated: bool = False

    # ------------------------------------------------------------------ #
    def all_files(self) -> list[FileNode]:
        return [f for m in self.modules for f in m.files]

    def find_file(self, rel_path: str) -> FileNode | None:
        rel = rel_path.replace("\\", "/")
        return next((f for f in self.all_files() if f.path == rel), None)

    def signatures(self) -> dict[str, str]:
        """path -> content_hash for every indexed file (the freshness baseline)."""
        return {f.path: f.content_hash for f in self.all_files()}

    # ------------------------------------------------------------------ #
    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(asdict(self), indent=indent, default=str)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "RepoIndex":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "RepoIndex":
        modules = [
            ModuleNode(
                name=m["name"],
                summary=m.get("summary", ""),
                depends_on=m.get("depends_on", []),
                files=[
                    FileNode(
                        path=f["path"], language=f["language"], loc=f["loc"],
                        summary=f.get("summary", ""),
                        symbols=[Symbol(**s) for s in f.get("symbols", [])],
                        imports=f.get("imports", []),
                        content_hash=f.get("content_hash", ""),
                    )
                    for f in m.get("files", [])
                ],
            )
            for m in data.get("modules", [])
        ]
        return cls(
            name=data["name"], root=data["root"], summary=data.get("summary", ""),
            modules=modules, entry_points=data.get("entry_points", []),
            languages=data.get("languages", {}), file_count=data.get("file_count", 0),
            total_loc=data.get("total_loc", 0), model_used=data.get("model_used", "heuristic"),
            version=data.get("version", INDEX_VERSION),
            generated_at=data.get("generated_at", ""),
            fingerprint=data.get("fingerprint", ""),
            truncated=data.get("truncated", False),
        )
