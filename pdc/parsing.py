"""Parsing dei messaggi dell'admin e calcolo dei piazzamenti."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Separatori accettati tra i due giocatori: " - ", " – ", " — ", " vs ", " v "
_SEP = re.compile(r"\s+(?:-|–|—|vs\.?|v)\s+", re.IGNORECASE)
# Numerazione iniziale opzionale: "1) ", "1. ", "- ", "• "
_PREFIX = re.compile(r"^\s*(?:\d+\s*[).:-]\s*|[-•*]\s+)")


class ParseError(ValueError):
    pass


def parse_matches(text: str) -> list[tuple[str, str]]:
    """Una partita per riga, formato 'GIOCATORE 1 - GIOCATORE 2'."""
    matches: list[tuple[str, str]] = []
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = _PREFIX.sub("", raw).strip()
        if not line:
            continue
        parts = _SEP.split(line)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            # fallback: trattino senza spazi, solo se ce n'è esattamente uno
            if line.count("-") == 1:
                parts = line.split("-")
            else:
                errors.append(f"riga {n}: «{raw.strip()}»")
                continue
        p1, p2 = parts[0].strip(), parts[1].strip()
        if not p1 or not p2:
            errors.append(f"riga {n}: «{raw.strip()}»")
            continue
        if p1.casefold() == p2.casefold():
            errors.append(f"riga {n}: stesso giocatore due volte")
            continue
        # Telegram: opzioni sondaggio max 100 caratteri
        if len(p1) > 100 or len(p2) > 100:
            errors.append(f"riga {n}: nome troppo lungo (max 100 caratteri)")
            continue
        matches.append((p1, p2))
    if errors:
        raise ParseError("Non ho capito queste righe:\n" + "\n".join(errors))
    if not matches:
        raise ParseError("Nessuna partita trovata.")
    return matches


_DATE_TIME = re.compile(
    r"^\s*(?:(?P<d>\d{1,2})[/.-](?P<m>\d{1,2})(?:[/.-](?P<y>\d{2,4}))?\s+)?(?P<H>\d{1,2})[:.](?P<M>\d{2})\s*$")
_RELATIVE = {"oggi": 0, "domani": 1, "dopodomani": 2}


def parse_start(text: str, tz: ZoneInfo, now: datetime | None = None) -> datetime:
    """Accetta '25/11/2026 15:30', '25/11 15:30', '15:30', 'domani 15:30'.

    Senza data: oggi, oppure domani se l'orario è già passato.
    """
    now = (now or datetime.now(tz)).astimezone(tz)
    s = text.strip().lower()
    day_offset = None
    for word, off in _RELATIVE.items():
        if s.startswith(word + " "):
            day_offset, s = off, s[len(word):].strip()
            break
    m = _DATE_TIME.match(s)
    if not m:
        raise ParseError("Formato orario non valido. Esempi: 25/11/2026 15:30, 25/11 15:30, domani 15:30, 15:30")
    hh, mm = int(m["H"]), int(m["M"])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ParseError("Ora non valida.")
    if m["d"]:
        if day_offset is not None:
            raise ParseError("Usa o la data o oggi/domani, non entrambi.")
        year = int(m["y"]) if m["y"] else now.year
        if year < 100:
            year += 2000
        try:
            d = date(year, int(m["m"]), int(m["d"]))
        except ValueError as e:
            raise ParseError(f"Data non valida: {e}") from None
        if not m["y"] and datetime.combine(d, time(hh, mm), tz) < now - timedelta(days=1):
            d = d.replace(year=year + 1)
    else:
        d = now.date() + timedelta(days=day_offset or 0)
        if day_offset is None and datetime.combine(d, time(hh, mm), tz) <= now:
            d += timedelta(days=1)
    return datetime.combine(d, time(hh, mm), tz)


def competition_positions(points: list[int]) -> list[int]:
    """Piazzamenti a pari merito stile sportivo: [5,5,3,2,2] -> [1,1,3,4,4]."""
    out: list[int] = []
    for i, p in enumerate(points):
        out.append(out[-1] if i and p == points[i - 1] else i + 1)
    return out


def fmt_dt(dt: datetime, tz: ZoneInfo) -> str:
    giorni = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
    d = dt.astimezone(tz)
    return f"{giorni[d.weekday()]} {d:%d/%m/%Y %H:%M}"
