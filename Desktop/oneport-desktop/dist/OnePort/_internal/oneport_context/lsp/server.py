# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
The Language Server. `oneport-context lsp ./repo` starts it over stdio; any LSP
editor (VS Code, Neovim, JetBrains) then gets hover-to-explain, answered from the
pre-built .context index — instant, no per-hover LLM call.

Requires the [lsp] extra (pygls). Kept separate from hover.py so the hover logic
stays importable/testable without pygls.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

try:
    from pygls.lsp.server import LanguageServer   # pygls >= 2.0
except ImportError:                                # pragma: no cover
    from pygls.server import LanguageServer        # pygls 1.x
from lsprotocol import types as lsp

from oneport_context.models import RepoIndex
from oneport_context.lsp.hover import build_hover, uri_to_rel, word_at


def _load_or_build_index(root: Path, index_path: Path) -> RepoIndex:
    if index_path.exists():
        try:
            return RepoIndex.load(index_path)
        except Exception:
            pass
    # No cached index — build a heuristic one (fast, no LLM) so hover still works.
    from oneport_context.indexer import build_index
    return asyncio.run(build_index(root, llm=None))


def run_lsp(root: Path, index_path: Path) -> None:
    index = _load_or_build_index(root, index_path)
    server = LanguageServer("oneport-context", "0.1.0")
    print(f"oneport-context LSP ready — {index.name}: {index.file_count} files indexed. "
          f"Connect your editor (stdio).", file=sys.stderr)

    @server.feature(lsp.TEXT_DOCUMENT_HOVER)
    def _hover(ls: LanguageServer, params: lsp.HoverParams):
        try:
            uri = params.text_document.uri
            rel = uri_to_rel(uri, root)
            if not rel:
                return None
            doc = ls.workspace.get_text_document(uri)
            lines = doc.source.splitlines()
            line_no = params.position.line
            line_text = lines[line_no] if 0 <= line_no < len(lines) else ""
            word = word_at(line_text, params.position.character)
            md = build_hover(index, rel, word)
            if not md:
                return None
            return lsp.Hover(contents=lsp.MarkupContent(kind=lsp.MarkupKind.Markdown, value=md))
        except Exception:
            return None

    server.start_io()
