# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Hover content builder — pure, no LSP dependency (so it's unit-testable).

The whole point: hover fires constantly, so it must be instant and free. This
reads only the pre-built index (file summary + the symbol under the cursor +
module role + dependencies). A live "explain deeper" LLM call is a separate,
on-demand action — never on hover.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from oneport_context.models import ModuleNode, RepoIndex

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def fs_path_from_uri(uri: str) -> str:
    """file:///C:/x -> C:/x  (and posix paths unchanged)."""
    p = urlparse(uri)
    path = unquote(p.path)
    if re.match(r"^/[A-Za-z]:", path):        # Windows drive path
        path = path[1:]
    return path


def uri_to_rel(uri: str, root: Path) -> str | None:
    try:
        fs = Path(fs_path_from_uri(uri)).resolve()
        return fs.relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def word_at(line_text: str, char: int) -> str:
    """Return the identifier under `char` in `line_text` (or '')."""
    for m in _IDENT.finditer(line_text):
        if m.start() <= char <= m.end():
            return m.group(0)
    return ""


def module_for(index: RepoIndex, rel_path: str) -> ModuleNode | None:
    return next((m for m in index.modules if any(f.path == rel_path for f in m.files)), None)


def build_hover(index: RepoIndex, rel_path: str, word: str) -> str | None:
    """Markdown tooltip for `word` in file `rel_path`, from the index. None if nothing useful."""
    f = index.find_file(rel_path)
    if f is None:
        return None
    module = module_for(index, rel_path)
    sym = next((s for s in f.symbols if s.name == word), None)

    parts: list[str] = []
    if sym:
        head = f"**{sym.name}** — {sym.kind}"
    else:
        head = f"`{rel_path.split('/')[-1]}`"
    if module:
        head += f"  ·  _{module.name}_"
    parts.append(head)

    if f.summary:
        parts.append(f.summary)
    if module and module.summary:
        parts.append(f"**Module:** {module.summary}")
    if module and module.depends_on:
        parts.append(f"_Depends on:_ {', '.join(module.depends_on)}")
    if sym:
        parts.append(f"_Defined at {rel_path}:{sym.line}_")

    parts.append("\n_oneport-context · press again / run `explain` for a deeper AI walkthrough_")
    return "\n\n".join(parts)
