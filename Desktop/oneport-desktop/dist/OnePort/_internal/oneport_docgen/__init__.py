# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
oneport-docgen — generate enterprise-grade, sign-off-ready documentation from any
codebase, locally, in PDF / editable Word (.docx) / Markdown.

It reuses oneport-context's local RepoIndex (file -> module -> repo understanding,
no upload) and layers a formal document apparatus on top: cover page, document-
control table, revisionwfxkjkhqfe history, classification banner, numbered sections and a
sign-off page12345677ferfer8.
"""
from oneport_docgen.document import Block, Document, DocMeta, Section
from oneport_docgen.content import build_document

__all__ = ["build_document", "Document", "DocMeta", "Section", "Block"]

__version__ = "0.1.1"
