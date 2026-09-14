# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""Render a Document to GitHub-flavored Markdown (plus the control apparatus)."""
from __future__ import annotations

from pathlib import Path

from oneport_docgen.document import Block, Document


def render_markdown(doc: Document, out_path: Path, brand=None) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_markdown(doc, brand), encoding="utf-8")
    return out_path


def _markdown(doc: Document, brand=None) -> str:
    m = doc.meta
    L: list[str] = []
    company = (brand.company if brand and brand.company else "").strip()

    # Title block ----------------------------------------------------------------
    if company:
        L.append(f"> **{company}**")
    L.append(f"# {m.title}")
    L.append("")
    L.append(f"`{m.classification.upper()}` · **{m.doc_type}** · v{m.version} · "
             f"_{m.status}_ · {m.doc_date}")
    L.append("")

    # Document control -----------------------------------------------------------
    L.append("## Document Control")
    L.append("")
    L.append("| Field | Value |")
    L.append("| --- | --- |")
    for k, v in m.control_rows():
        L.append(f"| {k} | {_esc(v)} |")
    L.append("")
    L.append("### Revision History")
    L.append("")
    L.append("| Version | Date | Author | Notes |")
    L.append("| --- | --- | --- | --- |")
    for r in m.revisions:
        L.append(f"| {r.version} | {r.date} | {_esc(r.author)} | {_esc(r.notes)} |")
    L.append("")

    # At a glance ----------------------------------------------------------------
    if doc.at_a_glance:
        L.append("## At a Glance")
        L.append("")
        L.append("| Field | Value |")
        L.append("| --- | --- |")
        for k, v in doc.at_a_glance.items():
            L.append(f"| {k} | {_esc(v)} |")
        L.append("")

    # Table of contents ----------------------------------------------------------
    L.append("## Contents")
    L.append("")
    for num, heading in doc.toc():
        L.append(f"{num}. {heading}")
    L.append("")

    # Sections -------------------------------------------------------------------
    for s in doc.sections:
        L.append(f"## {s.number}. {s.heading}")
        L.append("")
        for b in s.blocks:
            L.extend(_block_md(b))
        L.append("")

    # Sign-off -------------------------------------------------------------------
    L.append("## Approval & Sign-off")
    L.append("")
    L.append("| Role | Name | Signature | Date |")
    L.append("| --- | --- | --- | --- |")
    for role, people in (("Author", m.authors), ("Reviewer", m.reviewers),
                         ("Approver", m.approvers)):
        for person in (people or ["—"]):
            L.append(f"| {role} | {_esc(person)} |  |  |")
    L.append("")
    if company or (brand and brand.footer):
        foot = " · ".join(x for x in [company, brand.footer if brand else ""] if x)
        L.append(f"---\n\n_{foot}_")
    return "\n".join(L).rstrip() + "\n"


def _block_md(b: Block) -> list[str]:
    if b.kind == "para":
        return [b.text, ""]
    if b.kind == "subheading":
        return [f"### {b.text}", ""]
    if b.kind == "code":
        return ["```", b.text, "```", ""]
    if b.kind == "bullets":
        return [f"- {it}" for it in b.items] + [""]
    if b.kind == "table" and b.rows:
        head, *body = b.rows
        out = ["| " + " | ".join(_esc(c) for c in head) + " |",
               "| " + " | ".join("---" for _ in head) + " |"]
        out += ["| " + " | ".join(_esc(c) for c in row) + " |" for row in body]
        return out + [""]
    return []


def _esc(v: str) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")
