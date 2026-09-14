# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Render a Document to a formal, branded PDF with reportlab.

Layout: cover page (logo + title + classification banner) → document-control &
revision-history page → at-a-glance + table of contents → numbered sections →
approval / sign-off page. Every page carries a classification header and a
"Page X of Y" footer, the way an enterprise deliverable is expected to.
"""
from __future__ import annotations

from pathlib import Path

from oneport_docgen.document import Block, Document


def render_pdf(doc: Document, out_path: Path, brand=None) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.platypus import (
        BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle,
        PageBreak, HRFlowable, Image, KeepTogether,
    )

    from oneport_docgen.config import Brand
    b = brand or Brand()
    m = doc.meta

    PRIMARY = colors.HexColor(b.primary)
    ACCENT = colors.HexColor(b.accent)
    ACCENT2 = colors.HexColor(b.accent2)
    LGRAY = colors.HexColor("#F1F5F9")
    GRID = colors.HexColor("#CBD5E1")
    GRAY = colors.HexColor("#64748B")
    WHITE = colors.white
    font = b.font or "Helvetica"
    bold = f"{font}-Bold" if font == "Helvetica" else "Helvetica-Bold"
    base = getSampleStyleSheet()

    def ps(name, **kw):
        return ParagraphStyle(name, parent=base["Normal"], **kw)

    sCover = ps("cover", fontSize=30, textColor=WHITE, fontName=bold, alignment=TA_CENTER, leading=36, spaceAfter=6)
    sCoverSub = ps("csub", fontSize=13, textColor=colors.HexColor("#CBD5E1"), fontName=font, alignment=TA_CENTER, spaceAfter=4)
    sH1 = ps("h1", fontSize=15, textColor=ACCENT, fontName=bold, spaceBefore=14, spaceAfter=4)
    sH2 = ps("h2", fontSize=11.5, textColor=PRIMARY, fontName=bold, spaceBefore=9, spaceAfter=3)
    sBody = ps("body", fontSize=9.7, textColor=colors.HexColor("#1E293B"), fontName=font, leading=15, alignment=TA_JUSTIFY, spaceAfter=5)
    sBul = ps("bul", fontSize=9.7, textColor=colors.HexColor("#1E293B"), fontName=font, leftIndent=12, leading=14, spaceAfter=2)
    sTocL = ps("toc", fontSize=10.5, textColor=PRIMARY, fontName=font, leading=18)

    def safe(t: str) -> str:
        return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def rich(t: str) -> str:
        import re
        t = safe(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', t)
        return t

    # ---- page furniture (classification header + page X of Y footer) ---------
    classification = m.classification.upper()
    header_left = (b.company or "").strip() or "oneport-docgen"
    footer_extra = (b.footer or "").strip()

    def decorate(canvas, doct):
        canvas.saveState()
        w, h = A4
        # top classification strip
        canvas.setFillColor(PRIMARY)
        canvas.rect(0, h - 1.0 * cm, w, 1.0 * cm, fill=1, stroke=0)
        canvas.setFillColor(WHITE); canvas.setFont(bold, 7.5)
        canvas.drawString(1.4 * cm, h - 0.66 * cm, header_left)
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.drawCentredString(w / 2, h - 0.66 * cm, classification)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawRightString(w - 1.4 * cm, h - 0.66 * cm, f"{m.doc_type} · v{m.version}")
        canvas.setStrokeColor(ACCENT); canvas.setLineWidth(1.5)
        canvas.line(0, h - 1.0 * cm, w, h - 1.0 * cm)
        # bottom footer
        canvas.setStrokeColor(GRID); canvas.setLineWidth(0.5)
        canvas.line(1.4 * cm, 1.05 * cm, w - 1.4 * cm, 1.05 * cm)
        canvas.setFillColor(GRAY); canvas.setFont("Helvetica", 7.5)
        canvas.drawString(1.4 * cm, 0.62 * cm, footer_extra or classification.title())
        canvas.drawCentredString(w / 2, 0.62 * cm, m.title[:70])
        # "Page X of Y" is stamped by NumberedCanvas at save time (needs the total).
        canvas.restoreState()

    def cover_bg(canvas, doct):
        canvas.saveState()
        w, h = A4
        canvas.setFillColor(PRIMARY)
        canvas.rect(0, 0, w, h, fill=1, stroke=0)
        canvas.setFillColor(ACCENT)
        canvas.rect(0, h - 0.5 * cm, w, 0.5 * cm, fill=1, stroke=0)
        canvas.rect(0, 0, w, 0.5 * cm, fill=1, stroke=0)
        # classification badge, centered near bottom
        canvas.setFillColor(ACCENT2)
        badge_w = 6 * cm
        canvas.roundRect((w - badge_w) / 2, 3.2 * cm, badge_w, 1.0 * cm, 6, fill=1, stroke=0)
        canvas.setFillColor(WHITE); canvas.setFont("Helvetica-Bold", 11)
        canvas.drawCentredString(w / 2, 3.55 * cm, classification)
        canvas.restoreState()

    # ---- tables --------------------------------------------------------------
    def kv_table(rows, label_w=4.6, val_w=12.4, header=None):
        data = ([[header[0], header[1]]] if header else []) + [[k, Paragraph(rich(v), sBody)] for k, v in rows]
        t = Table(data, colWidths=[label_w * cm, val_w * cm])
        style = [
            ("BACKGROUND", (0, 0 + (1 if header else 0)), (0, -1), LGRAY),
            ("FONTNAME", (0, 0), (0, -1), bold),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TEXTCOLOR", (0, 0), (0, -1), PRIMARY),
            ("GRID", (0, 0), (-1, -1), 0.5, GRID),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ]
        if header:
            style += [("BACKGROUND", (0, 0), (-1, 0), PRIMARY), ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                      ("FONTNAME", (0, 0), (-1, 0), bold)]
        t.setStyle(TableStyle(style))
        return t

    def grid_table(rows, col_widths, header=True, head_bg=ACCENT):
        wrapped = []
        for i, row in enumerate(rows):
            wrapped.append([Paragraph(rich(c), sBody if i else ps("th", fontSize=8.5, textColor=WHITE, fontName=bold)) for c in row])
        t = Table(wrapped, colWidths=[c * cm for c in col_widths], repeatRows=1 if header else 0)
        style = [
            ("FONTSIZE", (0, 0), (-1, -1), 8.6),
            ("GRID", (0, 0), (-1, -1), 0.5, GRID),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LGRAY]),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]
        if header:
            style += [("BACKGROUND", (0, 0), (-1, 0), head_bg)]
        t.setStyle(TableStyle(style))
        return t

    # ---- build the flowables -------------------------------------------------
    story: list = []

    # Cover
    story.append(Spacer(1, 3.6 * cm))
    logo = b.logo_path()
    if logo:
        try:
            img = Image(str(logo))
            img._restrictSize(6 * cm, 2.6 * cm)
            img.hAlign = "CENTER"
            story += [img, Spacer(1, 0.6 * cm)]
        except Exception:
            pass
    if b.company:
        story.append(Paragraph(safe(b.company), sCoverSub))
        story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(safe(m.project), sCover))
    story.append(Paragraph(safe(m.doc_type), ps("dt", fontSize=15, textColor=ACCENT2, fontName=bold, alignment=TA_CENTER, spaceBefore=4)))
    story.append(Spacer(1, 0.8 * cm))
    story.append(Paragraph(f"Version {safe(m.version)} &nbsp;·&nbsp; {safe(m.status)} &nbsp;·&nbsp; {safe(m.doc_date)}", sCoverSub))
    story.append(PageBreak())

    # Document control + revisions
    story.append(Paragraph("Document Control", sH1))
    story.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceAfter=6))
    story.append(kv_table(m.control_rows()))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph("Revision History", sH1))
    story.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceAfter=6))
    rev_rows = [["Version", "Date", "Author", "Notes"]] + \
               [[r.version, r.date, r.author, r.notes] for r in m.revisions]
    story.append(grid_table(rev_rows, [2.2, 2.6, 4.2, 8.0]))
    story.append(PageBreak())

    # At a glance + ToC
    if doc.at_a_glance:
        story.append(Paragraph("At a Glance", sH1))
        story.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceAfter=6))
        story.append(kv_table(list(doc.at_a_glance.items())))
        story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph("Table of Contents", sH1))
    story.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceAfter=6))
    for num, heading in doc.toc():
        story.append(Paragraph(f'<b>{num}.</b>&nbsp;&nbsp;{safe(heading)}', sTocL))
    story.append(PageBreak())

    # Sections
    for s in doc.sections:
        story.append(Paragraph(f"{s.number}. {safe(s.heading)}", sH1))
        story.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceAfter=6))
        for blk in s.blocks:
            story += _pdf_blocks(blk, sBody, sH2, sBul, grid_table, rich, Paragraph, Spacer, KeepTogether)

    # Sign-off
    story.append(PageBreak())
    story.append(Paragraph("Approval & Sign-off", sH1))
    story.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceAfter=6))
    story.append(Paragraph("This document requires the following approvals before it is considered final.", sBody))
    story.append(Spacer(1, 0.3 * cm))
    signoff = [["Role", "Name", "Signature", "Date"]]
    for role, people in (("Author", m.authors), ("Reviewer", m.reviewers), ("Approver", m.approvers)):
        for person in (people or ["—"]):
            signoff.append([role, person, "", ""])
    story.append(grid_table(signoff, [3.0, 5.0, 5.0, 4.0], head_bg=PRIMARY))

    # ---- assemble with cover template + numbered pages -----------------------
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame = Frame(1.4 * cm, 1.3 * cm, A4[0] - 2.8 * cm, A4[1] - 2.7 * cm, id="body")
    cover_frame = Frame(1.4 * cm, 1.3 * cm, A4[0] - 2.8 * cm, A4[1] - 2.7 * cm, id="cover")
    doct = _NumberedDoc(str(out_path), pagesize=A4)
    doct.addPageTemplates([
        PageTemplate(id="Cover", frames=[cover_frame], onPage=cover_bg),
        PageTemplate(id="Body", frames=[frame], onPage=decorate),
    ])
    # first page uses the Cover template, then switch to Body
    from reportlab.platypus import NextPageTemplate
    story.insert(0, NextPageTemplate("Body"))
    doct.build(story)
    return out_path


def _pdf_blocks(blk: Block, sBody, sH2, sBul, grid_table, rich, Paragraph, Spacer, KeepTogether):
    out = []
    if blk.kind == "para":
        out.append(Paragraph(rich(blk.text), sBody))
    elif blk.kind == "subheading":
        out.append(Paragraph(rich(blk.text), sH2))
    elif blk.kind == "code":
        out.append(Paragraph(f'<font face="Courier">{rich(blk.text)}</font>', sBody))
    elif blk.kind == "bullets":
        for it in blk.items:
            out.append(Paragraph("•&nbsp;&nbsp;" + rich(it), sBul))
        out.append(Spacer(1, 4))
    elif blk.kind == "table" and blk.rows:
        ncols = len(blk.rows[0])
        total = 17.0
        widths = [total / ncols] * ncols
        out.append(grid_table(blk.rows, widths))
        out.append(Spacer(1, 6))
    return out


# --------------------------------------------------------------------------- #
# "Page X of Y" — the canonical two-pass NumberedCanvas.                         #
# The total isn't known until every page is laid out, so we buffer page state   #
# and stamp the footer number during save(), when the count is final.           #
# --------------------------------------------------------------------------- #
def _make_numbered():
    from reportlab.platypus import BaseDocTemplate
    from reportlab.pdfgen import canvas as canvas_mod
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm

    class NumberedCanvas(canvas_mod.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_states = []

        def showPage(self):
            self._saved_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_states)
            for state in self._saved_states:
                self.__dict__.update(state)
                if self._pageNumber > 1:            # skip the cover page
                    self._draw_page_number(total)
                canvas_mod.Canvas.showPage(self)
            canvas_mod.Canvas.save(self)

        def _draw_page_number(self, total):
            w, _ = A4
            self.setFont("Helvetica", 7.5)
            self.setFillColorRGB(0.39, 0.45, 0.55)
            self.drawRightString(w - 1.4 * cm, 0.62 * cm,
                                 f"Page {self._pageNumber} of {total}")

    class NumberedDoc(BaseDocTemplate):
        def build(self, flowables, **kw):
            super().build(flowables, canvasmaker=NumberedCanvas, **kw)

    return NumberedDoc, NumberedCanvas


_NumberedDoc, _NumberedCanvas = _make_numbered()
