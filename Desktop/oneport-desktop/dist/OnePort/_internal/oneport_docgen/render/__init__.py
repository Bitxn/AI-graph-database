# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""Format renderers for a Document. One structure in, many deliverables out."""
from __future__ import annotations

from pathlib import Path

from oneport_docgen.document import Document
from oneport_docgen.render.markdown import render_markdown
from oneport_docgen.render.pdf import render_pdf
from oneport_docgen.render.docx import render_docx

# format key -> (renderer, file extension)
RENDERERS = {
    "pdf": (render_pdf, ".pdf"),
    "docx": (render_docx, ".docx"),
    "md": (render_markdown, ".md"),
}

FORMATS = tuple(RENDERERS)


def render(doc: Document, fmt: str, out_path: Path, brand=None) -> Path:
    if fmt not in RENDERERS:
        raise ValueError(f"Unknown format {fmt!r}; choose from {', '.join(FORMATS)}")
    fn, _ext = RENDERERS[fmt]
    return fn(doc, out_path, brand)


def extension(fmt: str) -> str:
    return RENDERERS[fmt][1]


__all__ = ["render", "render_pdf", "render_docx", "render_markdown", "RENDERERS", "FORMATS", "extension"]
