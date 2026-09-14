# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""Render walkthrough slides as PNG images with Pillow (no browser, no poppler)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
BG = (13, 13, 13)
CARD = (22, 22, 22)
ACCENT = (62, 207, 142)
TEXT = (232, 232, 232)
DIM = (150, 150, 150)
FAINT = (95, 95, 95)
EDGE = (70, 70, 70)

_FONT_CANDIDATES = [
    "arial.ttf", "Arial.ttf", "segoeui.ttf",                       # Windows
    "/System/Library/Fonts/Supplemental/Arial.ttf",               # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",            # Linux
    "DejaVuSans.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_slide(path: Path, *, kind: str, title: str, subtitle: str = "",
                 bullets: list[str] | None = None, caption: str = "",
                 repo: str = "", modules: list[dict] | None = None,
                 highlight: str | None = None) -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # header
    d.text((60, 40), "oneport-context", font=_font(20), fill=ACCENT)
    if repo:
        d.text((60 + int(d.textlength("oneport-context", font=_font(20))) + 14, 42),
               f"· {repo}", font=_font(18), fill=FAINT)
    d.line((60, 78, W - 60, 78), fill=(40, 40, 40), width=1)

    # title
    ft = _font(56 if kind == "cover" else 44)
    for i, line in enumerate(_wrap(d, title, ft, W - 120)[:2]):
        d.text((60, 120 + i * (ft.size + 8)), line, font=ft, fill=TEXT)

    body_top = 120 + 2 * (ft.size + 8) + 10

    if kind == "architecture" and modules:
        _draw_diagram(d, modules, highlight, top=body_top)
    else:
        if subtitle:
            for i, line in enumerate(_wrap(d, subtitle, _font(26), W - 120)[:4]):
                d.text((60, body_top + i * 38), line, font=_font(26), fill=DIM)
            body_top += min(4, len(_wrap(d, subtitle, _font(26), W - 120))) * 38 + 16
        for b in (bullets or [])[:6]:
            d.ellipse((66, body_top + 10, 74, body_top + 18), fill=ACCENT)
            for j, line in enumerate(_wrap(d, b, _font(24), W - 160)[:2]):
                d.text((92, body_top + j * 32), line, font=_font(24), fill=TEXT)
            body_top += 32 * min(2, len(_wrap(d, b, _font(24), W - 160))) + 14

    # caption (subtitle-style narration) at the bottom
    if caption:
        cap_lines = _wrap(d, caption, _font(20), W - 120)[:2]
        cy = H - 70 - (len(cap_lines) - 1) * 26
        for line in cap_lines:
            d.text((60, cy), line, font=_font(20), fill=(180, 180, 180))
            cy += 26

    img.save(path, "PNG")


def _draw_diagram(d, modules: list[dict], highlight: str | None, top: int) -> None:
    n = len(modules)
    cols = max(1, min(4, int(n ** 0.5 + 0.999)))
    bw, bh, gx, gy = 250, 78, 40, 46
    x0 = (W - (cols * bw + (cols - 1) * gx)) // 2
    pos = {}
    for i, m in enumerate(modules):
        c, r = i % cols, i // cols
        pos[m["name"]] = (x0 + c * (bw + gx), top + 10 + r * (bh + gy))

    # edges
    for m in modules:
        for dep in m.get("depends_on", []):
            if dep in pos and m["name"] in pos:
                ax, ay = pos[m["name"]]; bx, by = pos[dep]
                d.line((ax + bw // 2, ay + bh // 2, bx + bw // 2, by + bh // 2), fill=EDGE, width=2)

    # boxes
    for m in modules:
        x, y = pos[m["name"]]
        hot = highlight and m["name"] == highlight
        d.rounded_rectangle((x, y, x + bw, y + bh), radius=10,
                            fill=(18, 40, 30) if hot else CARD,
                            outline=ACCENT if hot else EDGE, width=2 if hot else 1)
        name = m["name"] if len(m["name"]) <= 26 else m["name"][:25] + "…"
        d.text((x + 16, y + 16), name, font=_font(20), fill=TEXT if hot else (210, 210, 210))
        d.text((x + 16, y + 46), f"{len(m.get('files', []))} files · {m.get('loc', 0)} loc",
               font=_font(15), fill=DIM)
