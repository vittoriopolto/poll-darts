"""Lettura degli screenshot di diretta.it / Flashscore con l'OCR gratuito Tesseract.

Lo screenshot ha una riga per giocatore ("Cognome I."), due righe per partita; a destra l'orario
(partita da giocare) oppure i punti accanto a ogni nome (partita finita). Qui:

1. Tesseract legge le parole con la loro posizione (formato TSV);
2. le parole vengono raggruppate in righe e si tengono solo le righe che sembrano un nome
   ("Brooks B.", "van Gerwen M.");
3. due righe vicine formano una partita; una riga da sola (partita tagliata sul bordo) si scarta;
4. accanto a ogni nome si cerca il punteggio, tra le due righe l'orario.

Poi `resolve_name` abbina il nome letto a un giocatore già salvato nel bot, così si usa sempre lo stesso nome.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .livescore import _tokens, name_score

log = logging.getLogger(__name__)

class OcrError(Exception):
    """Tesseract mancante o immagine illeggibile."""


@dataclass
class Word:
    text: str
    left: int
    top: int
    width: int
    height: int

    @property
    def cy(self) -> float:
        return self.top + self.height / 2

    @property
    def right(self) -> int:
        return self.left + self.width


@dataclass
class ShotMatch:
    """Partita letta da uno screenshot. Punti None = da giocare (o non letti)."""
    p1: str
    p2: str
    s1: int | None = None
    s2: int | None = None
    time: str | None = None  # "14:30"

    @property
    def finished(self) -> bool:
        return self.s1 is not None and self.s2 is not None and self.s1 != self.s2


# Particelle minuscole ammesse all'inizio del cognome (van Gerwen, de Graaf…).
PARTICLES = {"van", "de", "der", "den", "von", "da", "di", "del", "la", "le", "du", "ten", "ter", "mac", "st"}
_INITIAL = re.compile(r"^[A-ZÀ-Þ](?:\.[A-ZÀ-Þ])?\.?$")
_SURNAME = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿĀ-ž'’\-]{2,}$")
_TIME = re.compile(r"^([01]?\d|2[0-3])[:.]([0-5]\d)$")
_SCORE = re.compile(r"^\d{1,2}$")


def _clean(text: str) -> str:
    return text.strip().strip("|¦,;:!“”\"")


def parse_tsv(tsv: str) -> list[Word]:
    words = []
    for line in tsv.splitlines():  # la riga di intestazione (se c'è) viene scartata sotto
        cols = line.split("\t")
        if len(cols) < 12 or not cols[11].strip():
            continue
        try:
            conf = float(cols[10])
            left, top, width, height = (int(c) for c in cols[6:10])
        except ValueError:
            continue
        if conf < 0:
            continue
        text = _clean(cols[11])
        if text:
            words.append(Word(text, left, top, width, height))
    return words


def _rows(words: list[Word]) -> list[list[Word]]:
    """Raggruppa le parole in righe orizzontali (centro verticale vicino)."""
    rows: list[list[Word]] = []
    hs = sorted(w.height for w in words)
    med = hs[len(hs) // 2] if hs else 0
    # parole altissime (bandiere, stelline lette come lettere) sballerebbero il raggruppamento
    words = [w for w in words if w.height <= med * 3]
    for w in sorted(words, key=lambda w: w.cy):
        for row in rows:
            ref = sum(x.cy for x in row) / len(row)
            h = max(x.height for x in row)
            if abs(w.cy - ref) <= max(h, w.height) * 0.45:
                row.append(w)
                break
        else:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda w: w.left)
    return rows


def _name_candidates(words: list[Word]) -> list[tuple[str, int, int]]:
    """Parole di una riga -> possibili nomi di giocatore ("Brooks B.", indice di inizio, indice dopo il nome).

    Davanti al nome possono esserci simboli letti male (bandiera, stellina, a volte una parola come "Fra"):
    per ogni punto di partenza si prova a leggere un nome; quale tenere lo decide l'allineamento a sinistra."""
    out = []
    for start in range(min(len(words), 4)):
        toks: list[str] = []
        for i in range(start, len(words)):
            t = words[i].text
            if _INITIAL.match(t) and toks:
                surname = toks
                if not any(_SURNAME.match(s) and s[0].isupper() for s in surname):
                    break
                if surname[0][0].islower() and surname[0].lower() not in PARTICLES:
                    break
                ini = t if t.endswith(".") else t + "."
                out.append((" ".join(surname + [ini]), start, i + 1))
                break
            # niente parole tutte maiuscole ("WS", "LIVE"): sono simboli letti male, non cognomi
            if (_SURNAME.match(t) and not t.isupper() and (t[0].isupper() or t.lower() in PARTICLES)
                    and len(toks) < 4):
                toks.append(t)
                continue
            break
    return out


@dataclass
class _NameRow:
    name: str
    cy: float
    h: float
    left: int
    score: int | None = None
    extra: list[Word] = field(default_factory=list)


def parse_words(words: list[Word], width: int, score_reader=None) -> list[ShotMatch]:
    """Parole con posizione -> partite (nell'ordine dall'alto in basso).

    `score_reader(cy, h)`, se c'è, rilegge i punti di una riga di nome in cui la prima lettura non li ha trovati
    (Tesseract salta spesso le cifre isolate)."""
    rows = _rows(words)
    cands = {id(row): [c for c in _name_candidates(row) if row[c[1]].left < width * 0.6] for row in rows}
    lefts = sorted(row[c[1]].left for row in rows for c in cands[id(row)])
    if not lefts:
        return []
    # colonna dei nomi: la posizione a sinistra più frequente (a meno di qualche pixel)
    tol = width * 0.03
    column = max(lefts, key=lambda x: sum(abs(y - x) <= tol for y in lefts))
    names: list[_NameRow] = []
    loose: list[Word] = []  # parole fuori dai nomi: orari, "11 L", punti su righe separate
    for row in rows:
        got = next((c for c in cands[id(row)] if abs(row[c[1]].left - column) <= tol * 2), None)
        if got:
            name, start, nxt = got
            part = row[start:nxt]
            h = max(w.height for w in part)
            nr = _NameRow(name, sum(w.cy for w in part) / len(part), h, row[start].left)
            right = [w for w in row[nxt:] if w.left > width * 0.45]
            scores = [w for w in right if _SCORE.match(w.text)]
            # "11 L" (al meglio di 11 leg) non è un punteggio
            scores = [w for w in scores if not any(o.text.upper() == "L" and 0 < o.left - w.right < w.height * 2
                                                   for o in right)]
            if scores:
                nr.score = int(scores[-1].text)
            nr.extra = [w for w in right if w not in scores]
            names.append(nr)
        else:
            loose.extend(row)
    if not names:
        return []
    names.sort(key=lambda n: n.cy)
    gaps = [b.cy - a.cy for a, b in zip(names, names[1:])]
    if not gaps:
        return []
    h = sorted(n.h for n in names)[len(names) // 2]
    d0 = min(g for g in gaps if g > 0) if any(g > 0 for g in gaps) else h
    limit = min(d0 * 1.3, h * 3.0)
    out: list[ShotMatch] = []
    i = 0
    while i + 1 < len(names):
        a, b = names[i], names[i + 1]
        if b.cy - a.cy <= limit:
            sm = ShotMatch(a.name, b.name)
            lo, hi = a.cy - a.h, b.cy + b.h
            for w in loose + a.extra + b.extra:
                tm = _TIME.match(w.text)
                if tm and lo <= w.cy <= hi and w.left > width * 0.45:
                    sm.time = f"{int(tm.group(1)):02d}:{tm.group(2)}"
                    break
            # con l'orario è da giocare: niente punti da cercare
            if score_reader and sm.time is None and (a.score is None or b.score is None):
                a.score = a.score if a.score is not None else score_reader(a.cy, a.h)
                b.score = b.score if b.score is not None else score_reader(b.cy, b.h)
            if a.score is not None and b.score is not None and sm.time is None:
                sm.s1, sm.s2 = a.score, b.score
            out.append(sm)
            i += 2
        else:
            i += 1  # nome senza avversario: partita tagliata sul bordo
    return out


def _prepare(data: bytes):
    """Scala di grigi, chiaro su scuro -> scuro su chiaro, ingrandisce le immagini piccole."""
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im).convert("L")
    if sum(im.resize((64, 64)).getdata()) / 4096 < 128:  # tema scuro (come l'app di diretta.it)
        im = ImageOps.invert(im)
    if im.width < 1000:
        k = 1000 / im.width
        im = im.resize((1000, int(im.height * k)), Image.LANCZOS)
    return im


# Tesseract arriva dentro il pacchetto pip "tesserocr" (niente da installare sul server, va bene il runtime
# Python di Render); il modello della lingua inglese è in assets/tessdata.
TESSDATA = Path(__file__).resolve().parent.parent / "assets" / "tessdata"
_lock = threading.Lock()


def available() -> bool:
    try:
        import tesserocr  # noqa: F401
    except ImportError:
        return False
    return (TESSDATA / "eng.traineddata").exists()


def _api(psm: int):
    import tesserocr

    return tesserocr.PyTessBaseAPI(path=str(TESSDATA), lang="eng", psm=psm)


def read_image_sync(data: bytes) -> list[ShotMatch]:
    if not available():
        raise OcrError("l'OCR (tesserocr) non è installato sul server.")
    import tesserocr

    try:
        im = _prepare(data)
    except Exception as e:
        raise OcrError(f"immagine non valida ({e})") from e
    with _lock:  # una lettura alla volta: poca RAM sul piano free
        api = _api(tesserocr.PSM.SPARSE_TEXT)
        digits = _api(tesserocr.PSM.SINGLE_LINE)
        digits.SetVariable("tessedit_char_whitelist", "0123456789:")
        try:
            api.SetImage(im)
            api.SetSourceResolution(300)  # senza, Tesseract perde gli "1" sottili (14:00 -> 4:00)
            tsv = api.GetTSVText(0)

            def score_reader(cy: float, h: float) -> int | None:
                """Punti a destra di un nome: ritaglia la riga (senza il riquadro LIVE) e legge solo cifre."""
                box = (int(im.width * 0.5), max(0, int(cy - h * 0.9)), int(im.width * 0.9),
                       min(im.height, int(cy + h * 0.9)))
                crop = im.crop(box)
                if crop.getextrema()[0] > 200:  # riga vuota
                    return None
                digits.SetImage(crop)
                digits.SetSourceResolution(300)
                nums = [t for t in digits.GetUTF8Text().split() if _SCORE.match(t)]
                return int(nums[-1]) if nums and int(nums[-1]) <= 30 else None

            return parse_words(parse_tsv(tsv), im.width, score_reader=score_reader)
        finally:
            api.End()
            digits.End()


async def read_image(data: bytes) -> list[ShotMatch]:
    return await asyncio.to_thread(read_image_sync, data)


# ---------------------------------------------------------------- nomi

def _is_full(name: str) -> bool:
    """'Bradley Brooks' sì, 'Brooks B.' no."""
    toks = _tokens(name)
    return len(toks) >= 2 and all(len(t) >= 2 for t in toks)


def order_known(names: list[str]) -> list[str]:
    """Nomi salvati (dal più recente) senza doppioni esatti, con i nomi completi davanti."""
    seen, out = set(), []
    for n in names:
        n = n.strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return sorted(out, key=lambda n: not _is_full(n))  # sort stabile: a parità resta l'ordine di recenza


@dataclass
class Resolved:
    name: str            # nome da usare (quello salvato se riconosciuto)
    status: str          # "ok" riconosciuto, "new" nuovo, "unsure" incerto
    read: str            # come l'ha letto l'OCR
    hint: str | None = None  # per "unsure": il giocatore che potrebbe essere


def resolve_name(read: str, known: list[str]) -> Resolved:
    """Abbina un nome letto ("Bialecki S.") a uno salvato ("Sebastian Białecki"). `known` da order_known."""
    full = [k for k in known if name_score(k, read) == 2]
    if full:
        first = full[0]
        others = [k for k in full[1:] if name_score(first, k) < 2]
        if others:
            return Resolved(first, "unsure", read, f"{first} o {others[0]}?")
        return Resolved(first, "ok", read)
    part = [k for k in known if name_score(read, k) >= 1 or name_score(k, read) >= 1]
    if part:
        return Resolved(read, "unsure", read, part[0])
    return Resolved(read, "new", read)


def same_player(a: str, b: str) -> bool:
    return name_score(a, b) == 2


def same_match(a1: str, a2: str, b1: str, b2: str) -> bool:
    """Stessa coppia di giocatori, in qualunque ordine."""
    return (same_player(a1, b1) and same_player(a2, b2)) or (same_player(a1, b2) and same_player(a2, b1))


def dedupe(matches: list[ShotMatch]) -> list[ShotMatch]:
    """Toglie le partite ripetute (stessa coppia letta in due screenshot che si sovrappongono)."""
    out: list[ShotMatch] = []
    for m in matches:
        dup = next((o for o in out if same_match(m.p1, m.p2, o.p1, o.p2)), None)
        if dup is None:
            out.append(m)
            continue
        # tiene le informazioni più complete
        if dup.time is None:
            dup.time = m.time
        if not dup.finished and m.finished:
            if same_player(dup.p1, m.p1):
                dup.s1, dup.s2 = m.s1, m.s2
            else:
                dup.s1, dup.s2 = m.s2, m.s1
    return out


__all__ = ["OcrError", "ShotMatch", "Resolved", "read_image", "read_image_sync", "parse_words", "parse_tsv",
           "resolve_name", "order_known", "same_match", "dedupe", "available"]
