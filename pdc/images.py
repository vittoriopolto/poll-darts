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


# ---------------------------------------------------------------- tabellone

_FR, _FW, _FB = (200, 16, 46), (250, 250, 250), (0, 56, 168)
_FG, _FY, _FK, _FO = (0, 135, 70), (255, 205, 0), (20, 20, 20), (255, 136, 62)
# bandiere a strisce: (orientamento, colori, pesi)
_STRIPES = {
    "NED": ("h", [(174, 28, 40), _FW, (33, 70, 139)]), "GER": ("h", [_FK, (221, 0, 0), _FY]),
    "AUT": ("h", [_FR, _FW, _FR]), "POL": ("h", [_FW, (220, 20, 60)]),
    "LTU": ("h", [(253, 185, 19), (0, 106, 68), (193, 39, 45)]),
    "ESP": ("h", [(170, 21, 27), (241, 191, 0), (241, 191, 0), (170, 21, 27)]),
    "HUN": ("h", [(206, 41, 57), _FW, (71, 112, 80)]), "LAT": ("h", [(158, 48, 57), _FW, (158, 48, 57)]),
    "IRL": ("v", [(22, 155, 98), _FW, _FO]), "BEL": ("v", [_FK, (253, 218, 36), (239, 51, 64)]),
    "ITA": ("v", [(0, 146, 70), _FW, (206, 43, 55)]), "FRA": ("v", [(0, 85, 164), _FW, (239, 65, 53)]),
    "POR": ("v", [(0, 102, 0), (255, 0, 0), (255, 0, 0)]), "CAN": ("v", [_FR, _FW, _FW, _FR]),
}
# bandiere a croce: (sfondo, croce, spostamento verso sinistra)
_CROSSES = {
    "ENG": (_FW, (206, 17, 36), 0), "NIR": (_FW, (206, 17, 36), 0), "SWE": ((0, 106, 167), (254, 204, 0), 3),
    "FIN": (_FW, (0, 47, 108), 3), "DEN": ((200, 16, 46), _FW, 3), "NOR": ((186, 12, 47), (0, 32, 91), 3),
    "SUI": ((218, 41, 28), _FW, 0),
}


def _flag(d: ImageDraw.ImageDraw, x: float, y: float, code: str | None, w: int = 26, h: int = 18) -> None:
    box = (x, y, x + w, y + h)
    if code in _STRIPES:
        ori, cols = _STRIPES[code]
        n = len(cols)
        for i, c in enumerate(cols):
            if ori == "h":
                d.rectangle((x, y + h * i / n, x + w, y + h * (i + 1) / n), fill=c)
            else:
                d.rectangle((x + w * i / n, y, x + w * (i + 1) / n, y + h), fill=c)
    elif code in _CROSSES:
        bg, fg, off = _CROSSES[code]
        d.rectangle(box, fill=bg)
        cx = x + w / 2 - off
        d.rectangle((cx - 2, y, cx + 2, y + h), fill=fg)
        d.rectangle((x, y + h / 2 - 2, x + w, y + h / 2 + 2), fill=fg)
    elif code == "SCO":
        d.rectangle(box, fill=(0, 94, 184))
        d.line((x, y, x + w, y + h), fill=_FW, width=4)
        d.line((x, y + h, x + w, y), fill=_FW, width=4)
    elif code == "WAL":
        d.rectangle((x, y, x + w, y + h / 2), fill=_FW)
        d.rectangle((x, y + h / 2, x + w, y + h), fill=(0, 177, 64))
        d.ellipse((x + w / 2 - 6, y + h / 2 - 5, x + w / 2 + 6, y + h / 2 + 5), fill=_FR)
    elif code == "CZE":
        d.rectangle((x, y, x + w, y + h / 2), fill=_FW)
        d.rectangle((x, y + h / 2, x + w, y + h), fill=(215, 20, 26))
        d.polygon([(x, y), (x + w / 2, y + h / 2), (x, y + h)], fill=(17, 69, 126))
    elif code in ("AUS", "NZL", "GBR"):
        d.rectangle(box, fill=(1, 33, 105))
        if code == "GBR":
            d.line((x, y, x + w, y + h), fill=_FW, width=4)
            d.line((x, y + h, x + w, y), fill=_FW, width=4)
            d.rectangle((x + w / 2 - 3, y, x + w / 2 + 3, y + h), fill=_FR)
            d.rectangle((x, y + h / 2 - 3, x + w, y + h / 2 + 3), fill=_FR)
        else:
            for px, py in ((8, 14), (19, 5), (22, 12), (17, 15)):
                d.ellipse((x + px - 2, y + py - 2, x + px + 2, y + py + 2),
                          fill=_FW if code == "AUS" else _FR)
            d.rectangle((x, y, x + 11, y + 7), fill=_FR)
    elif code == "JPN":
        d.rectangle(box, fill=_FW)
        d.ellipse((x + w / 2 - 5, y + h / 2 - 5, x + w / 2 + 5, y + h / 2 + 5), fill=(188, 0, 45))
    elif code == "USA":
        for i in range(7):
            d.rectangle((x, y + h * i / 7, x + w, y + h * (i + 1) / 7), fill=_FR if i % 2 == 0 else _FW)
        d.rectangle((x, y, x + 12, y + 10), fill=(60, 59, 110))
    else:  # nazione sconosciuta: la sigla in un riquadro grigio
        d.rectangle(box, fill=(60, 60, 68))
        f = _font(10, True)
        t = (code or "")[:3]
        d.text((x + (w - d.textlength(t, font=f)) / 2, y + 3), t, font=f, fill=WHITE)
    d.rectangle(box, outline=(0, 0, 0), width=1)


def _short_name(name: str, d: ImageDraw.ImageDraw, font, max_w: int) -> str:
    """Nei turni dopo il primo basta il cognome ('Michael van Gerwen' -> 'van Gerwen' -> 'v. Gerwen')."""
    first, _, rest = name.partition(" ")
    short = rest or first
    if d.textlength(short, font=font) > max_w:
        short = short.replace("van ", "v. ").replace("de ", "d. ")
    return short


def bracket_image(title: str, subtitle: str, rounds: list, names: list[str], updated: str = "") -> bytes:
    """Tabellone a eliminazione diretta: metà sinistra (rosso) e destra (blu), finale al centro.

    rounds: lista di turni (dal primo alla finale) di oggetti con p1, p2 (giocatori con name, seed, nat o
    None), s1, s2, winner; names: nomi dei turni nello stesso ordine."""
    n_side = len(rounds) - 1  # colonne per lato (la finale sta al centro)
    per_side = max(len(rounds[0]) // 2, 1)
    M, GAP, ROW = 30, 22, 30
    BOX = 2 * ROW + 2
    COL_R1, COL = 300, 188
    STEP = 84 if per_side > 1 else 120
    seeds = any(p and p.seed for m in rounds[0] for p in (m.p1, m.p2))
    side_w = COL_R1 + (n_side - 1) * COL + n_side * GAP
    W = max(2 * M + 2 * side_w + 240, 1600)
    final_w = W - 2 * M - 2 * side_w - 2 * GAP
    TOP = 330
    H = TOP + (per_side - 1) * STEP + BOX + 100 + (140 if per_side <= 2 else 0)

    img = _background(W, H)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 8), fill=RED)
    d.rectangle((0, H - 8, W, H), fill=BLUE)
    _paste_logo(img, 150, 150, 230)
    # titolo: prima parola bianca, il resto rosso (come la copertina)
    max_tw = W - 2 * 320
    tf = _shrink(d, title.upper(), 84, max_tw, _title_font)
    first, _, rest = title.upper().partition(" ")
    tw = d.textlength(title.upper(), font=tf)
    tx = (W - tw) / 2
    d.text((tx, 52), first, font=tf, fill=WHITE)
    if rest:
        d.text((tx + d.textlength(first + " ", font=tf), 52), rest, font=tf, fill=RED)
    sf = _shrink(d, subtitle, 34, max_tw)
    d.text(((W - d.textlength(subtitle, font=sf)) / 2, 160), subtitle, font=sf, fill=CREAM)
    if updated:
        uf = _font(24)
        d.text(((W - d.textlength(updated, font=uf)) / 2, 212), updated, font=uf, fill=MUTED)

    def col_x(side: int, r: int) -> tuple[int, int]:
        off = 0 if r == 0 else COL_R1 + GAP + (r - 1) * (COL + GAP)
        w = COL_R1 if r == 0 else COL
        return (M + off, w) if side == 0 else (W - M - off - w, w)

    lf = _font(22, True)
    for side in (0, 1):
        for r in range(n_side):
            x, w = col_x(side, r)
            lab = names[r]
            d.text((x + (w - d.textlength(lab, font=lf)) / 2, TOP - 50), lab, font=lf,
                   fill=RED if side == 0 else (120, 150, 255))

    line_c = (70, 70, 80)

    def draw_box(x, w, cy, m, side, full):
        y0 = cy - BOX / 2
        acc = RED if side == 0 else BLUE
        played = m is not None and m.winner in (1, 2)
        players = (m.p1, m.p2) if m is not None else (None, None)
        scores = (m.s1, m.s2) if m is not None else (None, None)
        has_score = played and scores[0] is not None and scores[1] is not None
        for i in range(2):
            y = y0 + i * (ROW + 2)
            d.rectangle((x, y, x + w, y + ROW), fill=PANEL if i == 0 else PANEL_ALT)
            p = players[i]
            if p is None:
                continue
            win = played and m.winner == i + 1
            lose = played and not win
            if win:
                d.rectangle((x, y, x + 5, y + ROW), fill=acc)
            tx = x + 10
            if full and seeds:
                if p.seed:
                    sf_ = _font(14, True)
                    st = str(p.seed)
                    d.text((tx + 22 - d.textlength(st, font=sf_), y + 8), st, font=sf_, fill=MUTED)
                tx += 28
            if p.nat:
                _flag(d, tx, y + 6, p.nat)
                tx += 34 if full else 33
            fs = 17 if full else 16
            nf = _font(fs) if lose else _font(fs, True)
            sc_w = 34 if has_score else 6
            room = x + w - sc_w - tx - 4
            name = p.name
            if not full:
                name = _short_name(name, d, nf, room)
            elif d.textlength(name, font=nf) > room and " " in name:
                f0, _, r0 = name.partition(" ")
                name = f"{f0[0]}. {r0}"
            d.text((tx, y + (5 if full else 6)), _fit(d, name, nf, room), font=nf, fill=MUTED if lose else WHITE)
            if has_score:
                s = str(scores[i])
                scf = _font(20, True)
                bx = x + w - 30
                d.rectangle((bx, y, x + w, y + ROW), fill=acc if win else (40, 40, 46))
                d.text((bx + 15 - d.textlength(s, font=scf) / 2, y + 3), s, font=scf, fill=WHITE if win else MUTED)

    def link(side, r, cy_a, cy_b, cy_next, hi_a, hi_b):
        x, w = col_x(side, r)
        acc = RED if side == 0 else BLUE
        if side == 0:
            ex, mx, nx = x + w, x + w + GAP / 2, x + w + GAP
        else:
            ex, mx, nx = x, x - GAP / 2, x - GAP
        for cy, hi in ((cy_a, hi_a), (cy_b, hi_b)):
            c = acc if hi else line_c
            d.line((ex, cy, mx, cy), fill=c, width=3)
            d.line((mx, cy, mx, cy_next), fill=c, width=3)
        d.line((mx, cy_next, nx, cy_next), fill=acc if (hi_a or hi_b) else line_c, width=3)

    final = rounds[-1][0]
    fx0 = M + side_w + GAP
    fx1 = fx0 + final_w
    fcy = TOP + BOX / 2 + (per_side - 1) * STEP / 2
    for side in (0, 1):
        centers = [TOP + BOX / 2 + i * STEP for i in range(per_side)]
        for r in range(n_side):
            ms = rounds[r][side * len(rounds[r]) // 2:(side + 1) * len(rounds[r]) // 2]
            x, w = col_x(side, r)
            if r > 0:
                prev = rounds[r - 1][side * len(rounds[r - 1]) // 2:(side + 1) * len(rounds[r - 1]) // 2]
                new_c = [(centers[2 * i] + centers[2 * i + 1]) / 2 for i in range(len(centers) // 2)]
                for i, ncy in enumerate(new_c):
                    link(side, r - 1, centers[2 * i], centers[2 * i + 1], ncy,
                         prev[2 * i].winner is not None, prev[2 * i + 1].winner is not None)
                centers = new_c
            for m, cy in zip(ms, centers):
                draw_box(x, w, cy, m, side, r == 0)
        x, w = col_x(side, n_side - 1)
        semi = rounds[n_side - 1][side] if n_side >= 1 else None
        hi = semi is not None and semi.winner is not None
        c = (RED if side == 0 else BLUE) if hi else line_c
        if side == 0:
            d.line((x + w, fcy, fx0, fcy), fill=c, width=3)
        else:
            d.line((x, fcy, fx1, fcy), fill=c, width=3)

    # finale al centro, più larga
    ff = _title_font(40)
    lab = names[-1]
    d.text((fx0 + (final_w - d.textlength(lab, font=ff)) / 2, fcy - 110), lab, font=ff, fill=CREAM)
    d.rectangle((fx0, fcy - BOX / 2 - 4, fx1, fcy + BOX / 2 + 4), outline=CREAM, width=2)
    draw_box(fx0 + 4, final_w - 8, fcy, final, 0, False)
    champ = final.winner_player() if final.winner in (1, 2) else None
    cf = _font(20, True)
    cl = "CAMPIONE"
    d.text((fx0 + (final_w - d.textlength(cl, font=cf)) / 2, fcy + 70), cl, font=cf, fill=MUTED)
    d.rounded_rectangle((fx0, fcy + 104, fx1, fcy + 150), radius=10, fill=RED if champ else None,
                        outline=CREAM, width=2)
    ct = champ.name if champ else "?"
    ctf = _font(24, True)
    ct = _fit(d, ct, ctf, final_w - 20)
    d.text((fx0 + (final_w - d.textlength(ct, font=ctf)) / 2, fcy + 112), ct, font=ctf,
           fill=WHITE if champ else CREAM)

    foot = "PDC180 POLL · " + ("numeri = teste di serie · " if seeds else "") + "in evidenza chi passa il turno"
    d.text((M, H - 58), foot, font=_font(22), fill=MUTED)
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


__all__ = ["cover_image", "ranking_image", "feedback_image", "bracket_image", "chunks"]
