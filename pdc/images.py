"""Generazione immagini (copertina sondaggi e classifica) con Pillow."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "assets"
FONT_REG = ASSETS / "fonts" / "DejaVuSans.ttf"
FONT_BOLD = ASSETS / "fonts" / "DejaVuSans-Bold.ttf"
# Se esiste assets/cover.png (o .jpg) viene usata quella al posto della copertina generata.
CUSTOM_COVERS = [ASSETS / "cover.png", ASSETS / "cover.jpg"]

BG = (14, 17, 22)
PANEL = (24, 29, 37)
PANEL_ALT = (30, 36, 46)
RED = (214, 40, 40)
GREEN = (30, 140, 75)
CREAM = (240, 230, 210)
MUTED = (150, 158, 170)
GOLD = (232, 186, 60)
SILVER = (192, 198, 206)
BRONZE = (199, 128, 72)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REG), size)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _fit(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _dartboard(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
    """Bersaglio stilizzato: 20 spicchi alternati, doppio/triplo rosso-verde, bull."""
    draw.ellipse((cx - r - 14, cy - r - 14, cx + r + 14, cy + r + 14), fill=(8, 8, 8))
    for i in range(20):
        a0, a1 = -99 + i * 18, -81 + i * 18
        light = i % 2 == 0
        ring = RED if light else GREEN
        draw.pieslice((cx - r, cy - r, cx + r, cy + r), a0, a1, fill=ring)
        r2 = int(r * 0.92)
        draw.pieslice((cx - r2, cy - r2, cx + r2, cy + r2), a0, a1, fill=CREAM if light else (20, 20, 20))
        r3 = int(r * 0.60)
        draw.pieslice((cx - r3, cy - r3, cx + r3, cy + r3), a0, a1, fill=ring)
        r4 = int(r * 0.53)
        draw.pieslice((cx - r4, cy - r4, cx + r4, cy + r4), a0, a1, fill=CREAM if light else (20, 20, 20))
    rb = int(r * 0.12)
    draw.ellipse((cx - rb, cy - rb, cx + rb, cy + rb), fill=GREEN)
    rb = int(r * 0.055)
    draw.ellipse((cx - rb, cy - rb, cx + rb, cy + rb), fill=RED)


def _shrink(draw: ImageDraw.ImageDraw, text: str, size: int, max_w: int, bold: bool = True,
            min_size: int = 28) -> ImageFont.FreeTypeFont:
    """Riduce il font finché il testo entra in max_w."""
    while size > min_size and draw.textlength(text, font=_font(size, bold)) > max_w:
        size -= 2
    return _font(size, bold)


def cover_image(title: str, session_name: str, lines: list[str]) -> bytes:
    """Copertina dei sondaggi. lines: righe di testo sotto il nome sessione."""
    for p in CUSTOM_COVERS:
        if p.exists():
            return p.read_bytes()
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    for i in range(-H, W, 90):  # bande diagonali
        d.polygon([(i, H), (i + 40, H), (i + 40 + H, 0), (i + H, 0)], fill=(18, 22, 29))
    _dartboard(d, 1075, 360, 175)

    x, tx, max_w = 70, 115, 760
    d.rectangle((x, 120, x + 12, 600), fill=RED)
    big = _shrink(d, title, 100, max_w)
    d.text((tx, 130), _fit(d, title, big, max_w), font=big, fill=(255, 255, 255))
    med = _shrink(d, session_name, 52, max_w)
    d.text((tx, 270), _fit(d, session_name, med, max_w), font=med, fill=GOLD)
    small = _font(32)
    y = 345
    for i, line in enumerate(lines[:3]):
        d.text((tx, y), _fit(d, line, small, max_w), font=small, fill=CREAM if i == 0 else MUTED)
        y += 46
    tag = _font(28, bold=True)
    label = "VOTA QUI SOTTO ↓"
    tw = d.textlength(label, font=tag)
    d.rounded_rectangle((tx, 520, tx + tw + 50, 580), radius=12, fill=RED)
    d.text((tx + 25, 533), label, font=tag, fill=(255, 255, 255))
    return _png(img)


def ranking_image(title: str, subtitle: str, rows: list[tuple[int, str, int, int]], footer: str = "") -> bytes:
    """rows: (posizione, nome, punti, partite con risultato votate)."""
    W = 1000
    head_h, row_h, foot_h = 215, 64, 70
    n = max(len(rows), 1)
    H = head_h + n * row_h + foot_h
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 8), fill=RED)
    _dartboard(d, W - 110, 95, 62)
    d.text((50, 40), _fit(d, title, _font(52, True), W - 260), font=_font(52, True), fill=(255, 255, 255))
    d.text((50, 110), _fit(d, subtitle, _font(28), W - 260), font=_font(28), fill=MUTED)
    d.text((W - 230, head_h - 34), "PUNTI", font=_font(20, True), fill=MUTED)
    d.text((W - 125, head_h - 34), "SU", font=_font(20, True), fill=MUTED)

    medal = {1: GOLD, 2: SILVER, 3: BRONZE}
    name_f, pos_f, pts_f, sm_f = _font(32, True), _font(30, True), _font(36, True), _font(26)
    if not rows:
        d.text((50, head_h + 14), "Nessun voto ancora.", font=_font(30), fill=MUTED)
    for i, (pos, name, pts, answered) in enumerate(rows):
        y = head_h + i * row_h
        d.rectangle((30, y + 4, W - 30, y + row_h - 4), fill=PANEL if i % 2 == 0 else PANEL_ALT)
        col = medal.get(pos)
        if col:
            d.ellipse((48, y + 12, 88, y + 52), fill=col)
            ptxt = str(pos)
            pw = d.textlength(ptxt, font=pos_f)
            d.text((68 - pw / 2, y + 15), ptxt, font=pos_f, fill=BG)
        else:
            ptxt = f"{pos}."
            d.text((52, y + 15), ptxt, font=pos_f, fill=MUTED)
        d.text((115, y + 13), _fit(d, name, name_f, W - 400), font=name_f, fill=(255, 255, 255))
        ps = str(pts)
        pw = d.textlength(ps, font=pts_f)
        d.text((W - 195 - pw / 2, y + 10), ps, font=pts_f, fill=col or CREAM)
        a = str(answered)
        aw = d.textlength(a, font=sm_f)
        d.text((W - 110 - aw / 2, y + 17), a, font=sm_f, fill=MUTED)
    if footer:
        d.text((50, H - foot_h + 20), _fit(d, footer, _font(22), W - 100), font=_font(22), fill=MUTED)
    return _png(img)


def chunks(rows: list, size: int = 30) -> list[list]:
    """Una classifica molto lunga viene divisa in più immagini."""
    return [rows[i:i + size] for i in range(0, len(rows), size)] or [[]]


__all__ = ["cover_image", "ranking_image", "chunks"]
