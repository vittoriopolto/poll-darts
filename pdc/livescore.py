"""Risultati delle partite PDC da TheSportsDB (API pubblica, chiamata solo su richiesta dell'admin).

TheSportsDB pubblica un "evento" per ogni giornata di torneo (es. "Players Championship Finals Day 1")
con tutti i risultati nel campo di testo strResult, una partita per riga:

    Rob Owen 6-5 Tom Bissell
    Jeffrey de Graaf v Gian van Veen      <- non ancora giocata

Qui si scaricano le giornate, si leggono le righe con il punteggio e si abbinano alle partite della
sessione confrontando i nomi dei giocatori (cognome, nome o iniziale).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date

log = logging.getLogger(__name__)

API_KEY = os.environ.get("THESPORTSDB_KEY", "123")  # "123" = chiave gratuita pubblica
PDC_LEAGUE_ID = "4554"
BASE_URL = "https://www.thesportsdb.com/api/v1/json"
TIMEOUT = 10

RESULT_RE = re.compile(r"^\s*(.+?)\s+(\d{1,2})\s*[-–]\s*(\d{1,2})\s+(.+?)\s*$")


@dataclass(frozen=True)
class Found:
    """Risultato trovato per una partita: vincitore (1/2) e punti nell'ordine della partita."""
    winner: int
    score1: int
    score2: int
    source: str  # riga originale, per mostrarla all'admin


class FetchError(Exception):
    """TheSportsDB non raggiungibile o risposta non valida."""


def _tokens(name: str) -> list[str]:
    """'🏴 Gary O'Connor' -> ['gary', 'oconnor'] (niente accenti, bandiere, punteggiatura)."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.split()


def name_score(ours: str, theirs: str) -> int:
    """Quanto il nome salvato nel bot corrisponde a quello di TheSportsDB.

    2 = tutte le parole combaciano (le iniziali valgono per il nome: 'Bissell T.' = 'Tom Bissell');
    1 = combacia solo il cognome (l'ultima parola che non è un'iniziale); 0 = diverso.
    """
    a, b = _tokens(ours), _tokens(theirs)
    if not a or not b:
        return 0

    def ok(t: str) -> bool:
        return t in b or (len(t) == 1 and any(x.startswith(t) for x in b))

    if all(ok(t) for t in a):
        return 2
    words = [t for t in a if len(t) >= 2]
    if words and words[-1] in b:
        return 1
    return 0


def parse_results(text: str) -> list[tuple[str, int, int, str, str]]:
    """Righe 'Nome A 6-4 Nome B' -> (nome_a, punti_a, punti_b, nome_b, riga)."""
    out = []
    for line in (text or "").splitlines():
        m = RESULT_RE.match(line)
        if not m:
            continue
        a, sa, sb, b = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        if sa == sb:
            continue
        out.append((a, sa, sb, b, line.strip()))
    return out


def match_results(matches, results) -> dict[int, Found]:
    """Abbina i risultati alle partite (oggetti con id, player1, player2). Ritorna {match_id: Found}.

    Una partita viene abbinata solo se entrambi i giocatori combaciano e il risultato è uno solo:
    nel dubbio non si indovina, l'admin lo mette a mano.
    """
    found: dict[int, Found] = {}
    for m in matches:
        best, cands = 0, []
        for a, sa, sb, b, line in results:
            straight = min(name_score(m.player1, a), name_score(m.player2, b))
            swapped = min(name_score(m.player1, b), name_score(m.player2, a))
            if straight >= swapped:
                score, s1, s2 = straight, sa, sb
            else:
                score, s1, s2 = swapped, sb, sa
            if score == 0:
                continue
            if score > best:
                best, cands = score, [(s1, s2, line)]
            elif score == best and (s1, s2, line) not in cands:
                cands.append((s1, s2, line))
        if len({(s1, s2) for s1, s2, _ in cands}) == 1:
            s1, s2, line = cands[0]
            found[m.id] = Found(1 if s1 > s2 else 2, s1, s2, line)
    return found


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "poll-darts-bot/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 (URL fisso https)
        return json.loads(r.read().decode("utf-8"))


async def fetch_day(day: date) -> list[str]:
    """Testi strResult degli eventi PDC di quel giorno."""
    q = urllib.parse.urlencode({"d": day.isoformat(), "l": PDC_LEAGUE_ID})
    url = f"{BASE_URL}/{API_KEY}/eventsday.php?{q}"
    try:
        data = await asyncio.to_thread(_get_json, url)
    except Exception as e:  # rete, timeout, JSON non valido
        log.warning("TheSportsDB %s: %s", day, e)
        raise FetchError(str(e)) from e
    events = (data or {}).get("events") or []
    return [ev.get("strResult") or "" for ev in events if isinstance(ev, dict)]


async def find_results(matches, days: list[date]) -> dict[int, Found]:
    """Scarica i risultati dei giorni indicati e li abbina alle partite."""
    texts: list[str] = []
    errors = 0
    for day in dict.fromkeys(days):
        try:
            texts += await fetch_day(day)
        except FetchError:
            errors += 1
    if errors and errors == len(set(days)):
        raise FetchError("TheSportsDB non risponde")
    results = [r for t in texts for r in parse_results(t)]
    return match_results(matches, results)
