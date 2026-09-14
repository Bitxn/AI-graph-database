# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
The structured Document — a format-agnostic representation of the deliverable.

`content.build_document()` produces one of these from a RepoIndex; each renderer
(PDF, DOCX, Markdown) walks the *same* structure. That separation is what lets one
generation pass emit a formal PDF, an editable Word file and Markdown in lockstep,
and it's the seam where future doctypes (SDD, SBOM, threat model) plug in — they
just build a different set of Sections.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


# --------------------------------------------------------------------------- #
# Content blocks — the vocabulary every renderer understands.                   #
# --------------------------------------------------------------------------- #
@dataclass
class Block:
    """One piece of section content. `kind` selects which fields are meaningful."""
    kind: str                                   # para | bullets | subheading | code | table
    text: str = ""                              # para / subheading / code
    items: list[str] = field(default_factory=list)      # bullets
    rows: list[list[str]] = field(default_factory=list)  # table (row 0 = header)

    # convenience constructors -------------------------------------------------
    @staticmethod
    def para(text: str) -> "Block":
        return Block("para", text=text)

    @staticmethod
    def sub(text: str) -> "Block":
        return Block("subheading", text=text)

    @staticmethod
    def bullets(items: list[str]) -> "Block":
        return Block("bullets", items=list(items))

    @staticmethod
    def table(rows: list[list[str]]) -> "Block":
        return Block("table", rows=[[str(c) for c in r] for r in rows])

    @staticmethod
    def code(text: str) -> "Block":
        return Block("code", text=text)


@dataclass
class Section:
    heading: str
    number: str = ""                            # "1", "2", … filled by build_document
    blocks: list[Block] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Document control metadata — the "enterprise" apparatus.                        #
# --------------------------------------------------------------------------- #
@dataclass
class Revision:
    version: str
    date: str
    author: str
    notes: str


@dataclass
class DocMeta:
    project: str
    doc_type: str = "Technical Design Document"
    title: str = ""                             # defaults to "<project> — <doc_type>"
    version: str = "1.0"
    status: str = "Draft"                       # Draft | In Review | Approved
    classification: str = "Confidential"        # Public | Internal | Confidential | Restricted
    doc_date: str = field(default_factory=lambda: date.today().isoformat())
    authors: list[str] = field(default_factory=list)
    reviewers: list[str] = field(default_factory=list)
    approvers: list[str] = field(default_factory=list)
    revisions: list[Revision] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.title:
            self.title = f"{self.project} — {self.doc_type}"
        if not self.revisions:
            self.revisions = [Revision(self.version, self.doc_date,
                                       self.authors[0] if self.authors else "—",
                                       "Initial version.")]

    def control_rows(self) -> list[list[str]]:
        """Rows for the document-control table (label / value pairs)."""
        return [
            ["Document Title", self.title],
            ["Document Type", self.doc_type],
            ["Version", self.version],
            ["Status", self.status],
            ["Classification", self.classification],
            ["Date", self.doc_date],
            ["Author(s)", ", ".join(self.authors) or "—"],
            ["Reviewer(s)", ", ".join(self.reviewers) or "—"],
            ["Approver(s)", ", ".join(self.approvers) or "—"],
        ]


@dataclass
class Document:
    meta: DocMeta
    summary: str = ""                           # one-paragraph elevator summary
    at_a_glance: dict[str, str] = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)

    def number_sections(self) -> None:
        for i, s in enumerate(self.sections, start=1):
            s.number = str(i)

    def toc(self) -> list[tuple[str, str]]:
        return [(s.number, s.heading) for s in self.sections]
