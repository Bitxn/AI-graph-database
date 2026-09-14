# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""Render the handbook to a styled PDF with reportlab."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from oneport_context.pdf.content import build_doc


async def make_pdf(index, out_path: Path, llm=None) -> Path:
    meta, markdown = await build_doc(index, llm=llm)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _render(out_path, index.name, meta, markdown)
    return out_path


def _render(out_path: Path, title: str, meta: dict, markdown: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, HRFlowable,
    )

    PRIMARY = colors.HexColor("#0F172A")
    ACCENT = colors.HexColor("#6366F1")
    ACCENT2 = colors.HexColor("#10B981")
    LGRAY = colors.HexColor("#F8FAFC")
    GRAY = colors.HexColor("#64748B")
    WHITE = colors.white
    base = getSampleStyleSheet()

    def ps(name, **kw):
        return ParagraphStyle(name, parent=base["Normal"], **kw)

    sTitle = ps("t", fontSize=26, textColor=WHITE, fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=5)
    sSub = ps("s", fontSize=12, textColor=GRAY, fontName="Helvetica", alignment=TA_CENTER, spaceAfter=4)
    sH1 = ps("h1", fontSize=16, textColor=ACCENT, fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=5)
    sH2 = ps("h2", fontSize=12, textColor=PRIMARY, fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=4)
    sBody = ps("b", fontSize=9.5, textColor=PRIMARY, fontName="Helvetica", leading=15, alignment=TA_JUSTIFY, spaceAfter=4)
    sBul = ps("bul", fontSize=9.5, textColor=PRIMARY, fontName="Helvetica", leftIndent=14, spaceAfter=3)

    def hf(canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setFillColor(PRIMARY)
        canvas.rect(0, h - 1.2 * cm, w, 1.2 * cm, fill=1, stroke=0)
        canvas.setFillColor(WHITE); canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(1 * cm, h - 0.8 * cm, "oneport-context  ·  Developer Handbook")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(w - 1 * cm, h - 0.8 * cm, datetime.now().strftime("%B %d, %Y"))
        canvas.setFillColor(GRAY); canvas.setFont("Helvetica", 7)
        canvas.drawRightString(w - 1 * cm, 0.6 * cm, f"Page {doc.page}")
        canvas.setStrokeColor(ACCENT); canvas.setLineWidth(2)
        canvas.line(0, h - 1.2 * cm, w, h - 1.2 * cm)
        canvas.restoreState()

    def safe(t):
        return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def md_to_story(text):
        out = []
        for line in text.split("\n"):
            if line.startswith("# "):
                out += [Spacer(1, 0.15 * cm), Paragraph(safe(line[2:]), sH1),
                        HRFlowable(width="100%", thickness=1, color=ACCENT, spaceAfter=4)]
            elif line.startswith("## "):
                out.append(Paragraph(safe(line[3:]), sH2))
            elif line.startswith("### "):
                out.append(Paragraph(safe(line[4:]), sH2))
            elif line.startswith("```"):
                continue
            elif line.strip().startswith(("- ", "* ")):
                t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe(line.strip()[2:]))
                t = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', t)
                out.append(Paragraph("• " + t, sBul))
            elif re.match(r"^\d+\.\s", line.strip()):
                out.append(Paragraph(safe(line.strip()), sBul))
            elif not line.strip():
                out.append(Spacer(1, 0.1 * cm))
            else:
                t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe(line))
                t = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', t)
                try:
                    out.append(Paragraph(t, sBody))
                except Exception:
                    out.append(Paragraph(safe(line)[:400], sBody))
        return out

    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.8 * cm, bottomMargin=1.5 * cm)
    story = [Spacer(1, 3 * cm)]

    cover = Table([[Paragraph("Developer Handbook", sTitle)],
                   [Paragraph("oneport-context", sSub)],
                   [Paragraph(safe(title)[:80], ps("pn", fontSize=15, textColor=ACCENT2,
                                                   fontName="Helvetica-Bold", alignment=TA_CENTER, spaceBefore=6))]],
                  colWidths=[17 * cm])
    cover.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PRIMARY), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                               ("TOPPADDING", (0, 0), (-1, -1), 18), ("BOTTOMPADDING", (0, 0), (-1, -1), 18)]))
    story += [cover, Spacer(1, 0.6 * cm)]
    if meta.get("tech_stack"):
        story.append(Paragraph("  ·  ".join(meta["tech_stack"][:10]),
                               ps("ts", fontSize=10, textColor=ACCENT, fontName="Helvetica-Bold", alignment=TA_CENTER)))
    story.append(PageBreak())

    # At a glance
    story.append(Paragraph("At a Glance", sH1))
    story.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceAfter=6))
    rows = [["Field", "Value"],
            ["Architecture", meta.get("architecture", "N/A")],
            ["Entry points", ", ".join(meta.get("entry_points", [])[:5]) or "N/A"],
            ["Tech stack", ", ".join(meta.get("tech_stack", [])[:8]) or "N/A"]]
    st = Table(rows, colWidths=[4 * cm, 13 * cm])
    st.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), PRIMARY), ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("BACKGROUND", (0, 1), (-1, -1), LGRAY),
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [st, Spacer(1, 0.4 * cm)]

    # Modules table
    if meta.get("modules"):
        story.append(Paragraph("Modules", sH1))
        story.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceAfter=6))
        md = [["Module", "Responsibility"]] + [[n[:34], (s or "")[:150]] for n, s in meta["modules"][:25]]
        mt = Table(md, colWidths=[5 * cm, 12 * cm])
        mt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), ACCENT), ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8),
                                ("BACKGROUND", (0, 1), (-1, -1), LGRAY),
                                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                                ("LEFTPADDING", (0, 0), (-1, -1), 6), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [mt, PageBreak()]

    story.extend(md_to_story(markdown))
    doc.build(story, onFirstPage=hf, onLaterPages=hf)
