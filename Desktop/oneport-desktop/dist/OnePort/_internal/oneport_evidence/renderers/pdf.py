"""
Sign-off-ready PDF evidence pack via reportlab — the deliverable a compliance
officer hands to an auditor: cover page, classification banner, executive summary,
control-coverage table, per-control detail, gate activity, and a signature block.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from oneport_evidence.result import EvidenceReport

_INK = colors.HexColor("#0d0d0d")
_MUTED = colors.HexColor("#6e6e80")
_LINE = colors.HexColor("#d9d9e0")
_BG = colors.HexColor("#f4f4f6")


def _date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "—"


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("Cover", parent=ss["Title"], fontSize=26, leading=30, textColor=_INK))
    ss.add(ParagraphStyle("CoverSub", parent=ss["Normal"], fontSize=12, textColor=_MUTED,
                          alignment=TA_CENTER, leading=18))
    ss.add(ParagraphStyle("H", parent=ss["Heading2"], fontSize=13, textColor=_INK, spaceBefore=14))
    ss.add(ParagraphStyle("Body", parent=ss["Normal"], fontSize=9.5, leading=14, textColor=_INK))
    ss.add(ParagraphStyle("Small", parent=ss["Normal"], fontSize=8, textColor=_MUTED))
    ss.add(ParagraphStyle("Banner", parent=ss["Normal"], fontSize=8, textColor=colors.white,
                          alignment=TA_CENTER))
    return ss


def render_pdf(report: EvidenceReport, out_path: str | Path, classification: str = "CONFIDENTIAL") -> Path:
    out_path = Path(out_path)
    ss = _styles()
    s = report.to_dict()["summary"]

    def banner(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(_INK)
        canvas.rect(0, A4[1] - 14, A4[0], 14, fill=1, stroke=0)
        canvas.rect(0, 0, A4[0], 14, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(A4[0] / 2, A4[1] - 10, classification)
        canvas.drawCentredString(A4[0] / 2, 5, f"{report.org} · {report.framework_name} · page {doc.page}")
        canvas.restoreState()

    story = []

    # ── cover ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 70 * mm))
    story.append(Paragraph(report.org, ss["Cover"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Compliance Evidence Pack", ParagraphStyle(
        "c2", parent=ss["Cover"], fontSize=16, textColor=_MUTED)))
    story.append(Spacer(1, 14 * mm))
    story.append(Paragraph(report.framework_name, ss["CoverSub"]))
    story.append(Paragraph(f"Reporting period {_date(report.period_start)} to "
                          f"{_date(report.period_end)}", ss["CoverSub"]))
    story.append(Paragraph(f"Generated {_date(report.generated_at)} (UTC)", ss["CoverSub"]))
    story.append(Spacer(1, 40 * mm))
    story.append(Paragraph(
        "Generated deterministically from local control-execution records. "
        "No data left the organization's environment; no generative model was used.",
        ss["Small"]))
    story.append(PageBreak())

    # ── executive summary ─────────────────────────────────────────────────────
    story.append(Paragraph("Executive summary", ss["H"]))
    story.append(Paragraph(
        f"During the reporting period, <b>{s['deploys_gated']}</b> deployment(s) across "
        f"<b>{s['repositories']}</b> repositor(y/ies) were subjected to Oneport pre-ship "
        f"controls, comprising <b>{s['total_gate_runs']}</b> automated control execution(s). "
        f"<b>{s['blocked_deploys']}</b> deployment(s) were blocked for policy violations and "
        f"required remediation before release. <b>{s['controls_operating']} of "
        f"{s['controls_total']}</b> mapped controls were operating during the period.",
        ss["Body"]))
    story.append(Spacer(1, 4 * mm))

    kpis = [["Deployments gated", "Blocked", "Control executions", "Controls operating"],
            [str(s["deploys_gated"]), str(s["blocked_deploys"]), str(s["total_gate_runs"]),
             f"{s['controls_operating']}/{s['controls_total']}"]]
    kt = Table(kpis, colWidths=[42 * mm] * 4)
    kt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _BG), ("TEXTCOLOR", (0, 0), (-1, 0), _MUTED),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5), ("FONTSIZE", (0, 1), (-1, 1), 15),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"), ("TEXTCOLOR", (0, 1), (-1, 1), _INK),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("TOPPADDING", (0, 1), (-1, 1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8), ("GRID", (0, 0), (-1, -1), 0.5, _LINE)]))
    story.append(kt)

    # ── control coverage ──────────────────────────────────────────────────────
    story.append(Paragraph("Control coverage", ss["H"]))
    rows = [["Control", "Name", "Status", "Runs", "Caught", "Last run"]]
    for c in report.controls:
        rows.append([c.control_id, Paragraph(c.name, ss["Small"]),
                     "Operating" if c.operating else "No activity",
                     str(c.runs), str(c.blocked), _date(c.last_run)])
    story.append(_grid(rows, [20 * mm, 60 * mm, 24 * mm, 14 * mm, 16 * mm, 22 * mm]))

    # ── control detail ─────────────────────────────────────────────────────────
    story.append(Paragraph("Control detail", ss["H"]))
    for c in report.controls:
        story.append(Paragraph(f"<b>{c.control_id} — {c.name}</b>", ss["Body"]))
        story.append(Paragraph(c.description, ss["Small"]))
        story.append(Paragraph(
            f"Enforcing gates: {', '.join(c.gates)} &nbsp;·&nbsp; "
            f"Executions: {c.runs} (passed {c.passed}, blocked {c.blocked}) &nbsp;·&nbsp; "
            f"Operating: {'Yes' if c.operating else 'No activity in period'}", ss["Small"]))
        story.append(Spacer(1, 3 * mm))

    # ── gate activity ──────────────────────────────────────────────────────────
    story.append(Paragraph("Gate activity", ss["H"]))
    grows = [["Gate", "Runs", "Passed", "Blocked", "Last run"]]
    for g in report.gate_summaries:
        grows.append([g.gate, str(g.runs), str(g.passed), str(g.blocked), _date(g.last_run)])
    if len(grows) == 1:
        grows.append(["no activity", "", "", "", ""])
    story.append(_grid(grows, [46 * mm, 22 * mm, 24 * mm, 24 * mm, 30 * mm]))

    # ── attestation ────────────────────────────────────────────────────────────
    story.append(Paragraph("Attestation", ss["H"]))
    story.append(Paragraph(
        f"This pack was generated from {report.org}'s local Oneport control ledger. Each "
        "execution count reflects an automated control that ran against a change before it "
        "was permitted to ship. The figures are a direct, auditable count of control "
        "executions.", ss["Body"]))
    story.append(Spacer(1, 8 * mm))
    sig = [["Prepared by", "", "Reviewed by", ""],
           ["Date", "", "Date", ""]]
    st = Table(sig, colWidths=[24 * mm, 50 * mm, 24 * mm, 50 * mm])
    st.setStyle(TableStyle([("LINEBELOW", (1, 0), (1, 0), 0.5, _INK),
                           ("LINEBELOW", (3, 0), (3, 0), 0.5, _INK),
                           ("LINEBELOW", (1, 1), (1, 1), 0.5, _INK),
                           ("LINEBELOW", (3, 1), (3, 1), 0.5, _INK),
                           ("FONTSIZE", (0, 0), (-1, -1), 9), ("TEXTCOLOR", (0, 0), (-1, -1), _MUTED),
                           ("TOPPADDING", (0, 0), (-1, -1), 10)]))
    story.append(st)

    doc = SimpleDocTemplate(str(out_path), pagesize=A4, topMargin=22 * mm, bottomMargin=20 * mm,
                           leftMargin=18 * mm, rightMargin=18 * mm, title="Compliance Evidence Pack")
    doc.build(story, onFirstPage=banner, onLaterPages=banner)
    return out_path


def _grid(rows, widths):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _INK), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, _LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _BG]),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5)]))
    return t
