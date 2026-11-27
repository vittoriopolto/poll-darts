"""Generazione immagini (copertina sondaggi e classifica) con Pillow, nello stile del logo PDC180."""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "assets"
FONT_REG = ASSETS / "fonts" / "DejaVuSans.ttf"
FONT_BOLD = ASSETS / "fonts" / "DejaVuSans-Bold.ttf"
FONT_TITLE = ASSETS / "fonts" / "LiberationSans-BoldItalic.ttf"  # corsivo grassetto come la scritta del logo
LOGO = ASSETS / "logo.jpg"
# Se esiste assets/cover.png (o .jpg) viene usata quella al posto della copertina generata.
CUSTOM_COVERS = [ASSETS / "cover.png", ASSETS / "cover.jpg"]

# Palette presa dal logo: nero, rosso "180", blu del bagliore, bianco, beige del bersaglio, verde dei settori.
BG = (8, 8, 10)
PANEL = (20, 20, 24)
PANEL_ALT = (28, 28, 33)
RED = (235, 22, 15)
BLUE = (30, 75, 235)
WHITE = (245, 245, 245)
CREAM = (230, 215, 180)
GREEN = (30, 150, 60)
MUTED = (150, 150, 160)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REG), size)


def _title_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_TITLE), size)


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


def _shrink(draw: ImageDraw.ImageDraw, text: str, size: int, max_w: int, maker=None,
            min_size: int = 28) -> ImageFont.FreeTypeFont:
    """Riduce il font finché il testo entra in max_w."""
    maker = maker or (lambda s: _font(s, True))
    while size > min_size and draw.textlength(text, font=maker(size)) > max_w:
        size -= 2
    return maker(size)


def _background(w: int, h: int) -> Image.Image:
    """Nero con bagliore rosso a sinistra e blu a destra, come il logo."""
    img = Image.new("RGB", (w, h), BG)
    glow = Image.new("RGB", (w, h), BG)
    g = ImageDraw.Draw(glow)
    r = int(max(w, h) * 0.45)
    g.ellipse((-r, h // 2 - r, r, h // 2 + r), fill=(120, 10, 6))
    g.ellipse((w - r, h // 2 - r, w + r, h // 2 + r), fill=(10, 30, 120))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(w, h) // 6))
    return Image.blend(img, glow, 0.85)


@lru_cache(maxsize=8)
def _logo(size: int) -> Image.Image | None:
    """Logo ritagliato a cerchio con bordo sfumato."""
    if not LOGO.exists():
        return None
    logo = Image.open(LOGO).convert("RGB").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    m = int(size * 0.02)
    ImageDraw.Draw(mask).ellipse((m, m, size - m, size - m), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(1, size // 120)))
    out = logo.convert("RGBA")
    out.putalpha(mask)
    return out


def _paste_logo(img: Image.Image, cx: int, cy: int, size: int) -> None:
    logo = _logo(size)
    if logo is not None:
        img.paste(logo, (cx - size // 2, cy - size // 2), logo)


def cover_image(title: str, session_name: str, lines: list[str]) -> bytes:
    """Copertina dei sondaggi. lines: righe di testo sotto il nome sessione."""
    for p in CUSTOM_COVERS:
        if p.exists():
            return p.read_bytes()
    W, H = 1280, 720
    img = _background(W, H)
    _paste_logo(img, 1040, 360, 440)
    d = ImageDraw.Draw(img)

    x, tx, max_w = 70, 112, 700
    d.rectangle((x, 110, x + 10, 610), fill=RED)
    # titolo: prima parola bianca, il resto rosso (come "PDC" bianco + "180" rosso)
    big = _shrink(d, title, 104, max_w, _title_font)
    first, _, rest = title.partition(" ")
    d.text((tx, 120), first, font=big, fill=WHITE)
    if rest:
        d.text((tx + d.textlength(first + " ", font=big), 120), rest, font=big, fill=RED)
    med = _shrink(d, session_name, 54, max_w)
    d.text((tx, 262), _fit(d, session_name, med, max_w), font=med, fill=WHITE)
    small = _font(32)
    y = 340
    for i, line in enumerate(lines[:4]):
        d.text((tx, y), _fit(d, line, small, max_w), font=small, fill=CREAM if i == 0 else MUTED)
        y += 46
    tag = _font(28, bold=True)
    label = "VOTA QUI SOTTO ↓"
    tw = d.textlength(label, font=tag)
    d.rounded_rectangle((tx, 540, tx + tw + 50, 600), radius=12, fill=RED)
    d.text((tx + 25, 553), label, font=tag, fill=WHITE)
    return _png(img)


def ranking_image(title: str, subtitle: str, rows: list[tuple[int, str, int, int]], footer: str = "") -> bytes:
    """rows: (posizione, nome, punti, partite con risultato votate)."""
    W = 1000
    head_h, row_h, foot_h = 225, 64, 70
    n = max(len(rows), 1)
    H = head_h + n * row_h + foot_h
    img = _background(W, H)
    _paste_logo(img, W - 108, 100, 170)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 8), fill=RED)
    tf = _shrink(d, title, 58, W - 300, _title_font)
    d.text((50, 40), title, font=tf, fill=WHITE)
    d.text((50, 118), _fit(d, subtitle, _font(26), W - 300), font=_font(26), fill=CREAM)
    hf = _font(20, True)
    for label, cx in (("PUNTI", W - 300), ("SU", W - 195), ("%", W - 95)):
        d.text((cx - d.textlength(label, font=hf) / 2, head_h - 30), label, font=hf, fill=MUTED)

    # podio con i colori del logo: rosso, blu, beige
    podium = {1: RED, 2: BLUE, 3: CREAM}
    name_f, pos_f, pts_f, sm_f = _font(32, True), _font(28, True), _font(36, True), _font(26)
    if not rows:
        d.text((50, head_h + 14), "Nessun voto ancora.", font=_font(30), fill=MUTED)
    for i, (pos, name, pts, answered) in enumerate(rows):
        y = head_h + i * row_h
        d.rectangle((30, y + 4, W - 30, y + row_h - 4), fill=PANEL if i % 2 == 0 else PANEL_ALT)
        col = podium.get(pos)
        if col:
            d.rectangle((30, y + 4, 36, y + row_h - 4), fill=col)
            d.ellipse((48, y + 12, 88, y + 52), fill=col)
            ptxt = str(pos)
            pw = d.textlength(ptxt, font=pos_f)
            d.text((68 - pw / 2, y + 16), ptxt, font=pos_f, fill=BG if col == CREAM else WHITE)
        else:
            d.text((52, y + 16), f"{pos}.", font=pos_f, fill=MUTED)
        d.text((115, y + 13), _fit(d, name, name_f, W - 490), font=name_f, fill=WHITE)
        ps = str(pts)
        pw = d.textlength(ps, font=pts_f)
        d.text((W - 300 - pw / 2, y + 10), ps, font=pts_f, fill=col or WHITE)
        a = str(answered)
        aw = d.textlength(a, font=sm_f)
        d.text((W - 195 - aw / 2, y + 17), a, font=sm_f, fill=MUTED)
        pc = f"{(pts * 200 + answered) // (2 * answered)}%" if answered else "–"
        cw = d.textlength(pc, font=sm_f)
        d.text((W - 95 - cw / 2, y + 17), pc, font=sm_f, fill=CREAM)
    if footer:
        d.text((50, H - foot_h + 22), _fit(d, footer, _font(22), W - 100), font=_font(22), fill=MUTED)
    return _png(img)


def feedback_image(title: str) -> bytes:
    """Immagine piccola per /consigli: invito a dare feedback con freccia verso il bottone."""
    W, H = 900, 420
    img = _background(W, H)
    _paste_logo(img, 755, 200, 250)
    d = ImageDraw.Draw(img)
    x, tx, max_w = 50, 84, 520
    d.rectangle((x, 60, x + 8, 300), fill=RED)
    # titolo come la copertina: prima parola bianca, il resto rosso
    big = _shrink(d, title, 64, max_w, _title_font)
    first, _, rest = title.partition(" ")
    d.text((tx, 58), first, font=big, fill=WHITE)
    if rest:
        d.text((tx + d.textlength(first + " ", font=big), 58), rest, font=big, fill=RED)
    head = "DAMMI QUALCHE FEEDBACK"
    d.text((tx, 152), head, font=_shrink(d, head, 36, max_w), fill=WHITE)
    d.text((tx, 196), "sul poll game: bastano pochi click", font=_font(24), fill=CREAM)
    d.text((tx, 234), "i tuoi consigli ci aiutano a migliorarlo", font=_font(21), fill=MUTED)
    # etichetta "RISPONDI QUI SOTTO" con freccia verso il basso
    tag = _font(30, bold=True)
    label = "RISPONDI QUI SOTTO"
    tw = d.textlength(label, font=tag)
    y0, y1 = 300, 364
    d.rounded_rectangle((tx, y0, tx + tw + 130, y1), radius=14, fill=RED)
    d.text((tx + 28, y0 + 14), label, font=tag, fill=WHITE)
    ax = tx + tw + 78
    d.rectangle((ax - 7, y0 + 12, ax + 7, y0 + 34), fill=WHITE)
    d.polygon([(ax - 20, y0 + 32), (ax + 20, y0 + 32), (ax, y0 + 54)], fill=WHITE)
    return _png(img)


def chunks(rows: list, size: int = 30) -> list[list]:
    """Una classifica lunga viene divisa in più immagini: prima quante ne servono (max `size` righe
    l'una), poi le righe distribuite in modo equo (es. 80 righe → 3 immagini da 27, 27, 26)."""
    if not rows:
        return [[]]
    n = -(-len(rows) // size)  # immagini necessarie
    base, extra = divmod(len(rows), n)
    out, start = [], 0
    for i in range(n):
        end = start + base + (1 if i < extra else 0)
        out.append(rows[start:end])
        start = end
    return out


__all__ = ["cover_image", "ranking_image", "feedback_image", "chunks"]
