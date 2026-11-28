"""Lettura degli screenshot: layout (senza Tesseract) e, se Tesseract c'è, un'immagine disegnata al volo."""
import io
from pathlib import Path

import pytest

from pdc import ocr
from pdc.ocr import ShotMatch, Word

W = 1206


def w(text, left, top, h=26):
    return Word(text, left, top, max(10, len(text) * 18), h)


def rows_for(matches, top=700, block=160, gap=62, times=None, scores=None):
    """Parole come le dà Tesseract per un elenco di diretta.it (con rumore delle bandiere)."""
    out = []
    for i, (a, b) in enumerate(matches):
        y = top + i * block
        out += [w("na", 96, y + 15, 13)] + [w(t, 170 + 120 * k, y) for k, t in enumerate(a.split())]
        out += [w("a", 96, y + gap + 15, 13)] + [w(t, 170 + 120 * k, y + gap) for k, t in enumerate(b.split())]
        if times:
            out += [w("11", 876, y + gap // 2), w("L", 925, y + gap // 2), w(times[i], 999, y + gap // 2)]
        if scores:
            out += [w(str(scores[i][0]), 880, y), w(str(scores[i][1]), 880, y + gap)]
    return out


def test_parse_scheduled_with_cut_and_noise():
    words = [w("Players", 170, 500), w("Championship", 300, 500), w("Finals", 520, 500)]
    words += [w("Scommesse", 435, 409, 14), w("Bonus.", 436, 430, 14), w("T&C", 502, 430, 14)]
    words += [w("Kist", 170, 602), w("C", 260, 602)]  # metà di una partita tagliata in alto
    words += rows_for([("Brooks B.", "Huybrechts K"), ("Labanauskas D.", "van Gerwen M.")],
                      times=["14:00", "16:00"])
    words += [w("Bialecki", 170, 1100), w("S.", 330, 1100)]  # tagliata in basso
    got = ocr.parse_words(words, W)
    assert got == [ShotMatch("Brooks B.", "Huybrechts K.", time="14:00"),
                   ShotMatch("Labanauskas D.", "van Gerwen M.", time="16:00")]


def test_parse_finished_scores():
    words = rows_for([("Greaves B.", "Kist C."), ("Smith M.", "Zonneveld N.")], scores=[(6, 3), (4, 6)])
    got = ocr.parse_words(words, W)
    assert [(m.p1, m.s1, m.s2, m.p2) for m in got] == [("Greaves B.", 6, 3, "Kist C."), ("Smith M.", 4, 6, "Zonneveld N.")]
    assert all(m.finished for m in got)


def test_score_reader_fallback():
    words = rows_for([("Greaves B.", "Kist C.")])
    seen = []

    def reader(cy, h):
        seen.append(cy)
        return 6 if len(seen) == 1 else 2
    got = ocr.parse_words(words, W, score_reader=reader)
    assert (got[0].s1, got[0].s2) == (6, 2)
    # con l'orario la partita è da giocare: il lettore dei punti non viene chiamato
    seen.clear()
    got = ocr.parse_words(rows_for([("Greaves B.", "Kist C.")], times=["15:00"]), W, score_reader=reader)
    assert seen == [] and not got[0].finished


def test_resolve_names():
    known = ocr.order_known(["Brooks B.", "Bradley Brooks", "Sebastian Białecki", "Ross Smith", "Michael Smith",
                             "Kim Huybrechts"])
    assert known[0] == "Bradley Brooks"  # i nomi completi prima delle abbreviazioni
    r = ocr.resolve_name("Brooks B.", known)
    assert (r.name, r.status) == ("Bradley Brooks", "ok")
    assert ocr.resolve_name("Bialecki S.", known).name == "Sebastian Białecki"
    assert ocr.resolve_name("Smith M.", known).name == "Michael Smith"
    r = ocr.resolve_name("Smith J.", known)
    assert r.status == "unsure" and r.name == "Smith J." and "Smith" in r.hint
    assert ocr.resolve_name("Pietreczko R.", known).status == "new"


def test_dedupe_overlapping_screenshots():
    a = [ShotMatch("Brooks B.", "Huybrechts K.", time="14:00"), ShotMatch("Smith M.", "Zonneveld N.")]
    b = [ShotMatch("Huybrechts K.", "Brooks B."), ShotMatch("Smith M.", "Zonneveld N.", 6, 4)]
    got = ocr.dedupe(a + b)
    assert len(got) == 2 and got[0].time == "14:00" and (got[1].s1, got[1].s2) == (6, 4)


def _draw(rows, scores=None, times=None) -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    fonts = Path(__file__).resolve().parents[1] / "assets" / "fonts"
    f = ImageFont.truetype(str(fonts / "DejaVuSans.ttf"), 34)
    fs = ImageFont.truetype(str(fonts / "DejaVuSans.ttf"), 28)
    im = Image.new("RGB", (W, 300 + 160 * len(rows)), (12, 22, 30))
    d = ImageDraw.Draw(im)
    y = 120
    for i, (a, b) in enumerate(rows):
        d.rectangle((95, y + 8, 140, y + 36), fill=(200, 30, 30))
        d.rectangle((95, y + 70, 140, y + 98), fill=(30, 30, 200))
        d.text((170, y), a, font=f, fill="white")
        d.text((170, y + 62), b, font=f, fill="white")
        if scores:
            d.text((870, y), str(scores[i][0]), font=f, fill="white")
            d.text((870, y + 62), str(scores[i][1]), font=f, fill="white")
        if times:
            d.text((990, y + 32), times[i], font=fs, fill="white")
        y += 160
    d.text((170, y), "Rock J.", font=f, fill="white")  # tagliata
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.skipif(not ocr.available(), reason="tesserocr non installato")
def test_tesseract_real_image():
    rows = [("Greaves B.", "Kist C."), ("Bialecki S.", "Wattimena J."), ("van Gerwen M.", "Labanauskas D.")]
    got = ocr.read_image_sync(_draw(rows, scores=[(6, 3), (5, 6), (6, 2)]))
    assert [(m.p1, m.s1, m.s2, m.p2) for m in got] == [
        ("Greaves B.", 6, 3, "Kist C."), ("Bialecki S.", 5, 6, "Wattimena J."), ("van Gerwen M.", 6, 2, "Labanauskas D.")]
    got = ocr.read_image_sync(_draw(rows, times=["14:00", "14:30", "15:00"]))
    assert [(m.p1, m.p2, m.time, m.finished) for m in got] == [
        ("Greaves B.", "Kist C.", "14:00", False), ("Bialecki S.", "Wattimena J.", "14:30", False),
        ("van Gerwen M.", "Labanauskas D.", "15:00", False)]


def test_flag_junk_before_name():
    # bandiera letta come parola maiuscola: vince il nome allineato alla colonna
    words = rows_for([("Sedlacek K.", "Mansell M."), ("Greaves B.", "Owen R.")])
    words += [w("Hi", 96, 700 + 62), w("Ea", 96, 700 + 160 + 62)]
    got = ocr.parse_words(words, W)
    assert [(m.p1, m.p2) for m in got] == [("Sedlacek K.", "Mansell M."), ("Greaves B.", "Owen R.")]
    # e se arriva lo stesso, l'abbinamento lo toglie
    known = ["Mickey Mansell", "James Hurrell"]
    r = ocr.resolve_name("Hi Mansell M.", known)
    assert (r.name, r.status, r.read) == ("Mickey Mansell", "ok", "Mansell M.")
    assert ocr.resolve_name("La Ltd Hurrell J.", known).name == "James Hurrell"
    assert ocr.resolve_name("Hi Rock J.", known).status == "new"
