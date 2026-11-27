"""Tabellone a eliminazione diretta: accoppiamenti del 1° turno salvati per evento, risultati presi dalle
partite delle sessioni dell'evento (abbinate per nome) e vincitori fatti avanzare turno dopo turno."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .livescore import name_score
from .parsing import ParseError

# Bandiere emoji -> codice a 3 lettere usato per disegnarle.
_ISO2 = {
    "NL": "NED", "DE": "GER", "AT": "AUT", "PL": "POL", "LT": "LTU", "ES": "ESP", "IE": "IRL", "BE": "BEL",
    "SE": "SWE", "CZ": "CZE", "AU": "AUS", "LV": "LAT", "PT": "POR", "HU": "HUN", "IT": "ITA", "FR": "FRA",
    "FI": "FIN", "DK": "DEN", "NO": "NOR", "CH": "SUI", "JP": "JPN", "CA": "CAN", "US": "USA", "NZ": "NZL",
    "ZA": "RSA", "PH": "PHI", "HK": "HKG", "HR": "CRO", "GB": "GBR", "GI": "GIB", "CN": "CHN", "IN": "IND",
}
_SUBDIV = {"gbeng": "ENG", "gbsct": "SCO", "gbwls": "WAL"}
# Codici scritti a mano: accettiamo anche le sigle a 2 lettere e qualche alias.
_ALIASES = {"NL": "NED", "DE": "GER", "UK": "GBR", "SCT": "SCO", "WLS": "WAL", "NI": "NIR", "IE": "IRL"}
KNOWN = set(_ISO2.values()) | set(_SUBDIV.values()) | {"NIR"}


@dataclass
class Player:
    name: str
    seed: int | None = None
    nat: str | None = None

    def to_json(self) -> list:
        return [self.seed, self.name, self.nat]

    @classmethod
    def from_json(cls, v: list) -> "Player":
        return cls(seed=v[0], name=v[1], nat=v[2])


@dataclass
class BMatch:
    p1: Player | None
    p2: Player | None
    s1: int | None = None
    s2: int | None = None
    winner: int | None = None  # 1 / 2, None = da giocare

    def winner_player(self) -> Player | None:
        return {1: self.p1, 2: self.p2}.get(self.winner)


def _flag_code(tok: str) -> str | None:
    """Bandiera emoji -> codice (🇳🇱 -> NED, 🏴󠁧󠁢󠁳󠁣󠁴󠁿 -> SCO)."""
    ri = [ord(c) - 0x1F1E6 for c in tok if 0x1F1E6 <= ord(c) <= 0x1F1FF]
    if len(ri) == 2:
        iso = chr(65 + ri[0]) + chr(65 + ri[1])
        return _ISO2.get(iso, iso)
    tags = "".join(chr(ord(c) - 0xE0000) for c in tok if 0xE0061 <= ord(c) <= 0xE007A)
    return _SUBDIV.get(tags)


def parse_player(text: str) -> Player:
    """'1 Wessel Nijman NED', '(1) 🇳🇱 Wessel Nijman', 'Nijman' -> Player."""
    words = text.strip().split()
    seed = nat = None
    out = []
    for w in words:
        code = _flag_code(w)
        if code:
            nat = code
            continue
        if not any(ch.isalnum() for ch in w):  # altra emoji / simbolo
            continue
        m = re.fullmatch(r"\(?\[?(\d{1,3})\]?\)?\.?", w)
        if m and seed is None and not out:
            seed = int(m.group(1))
            continue
        out.append(w)
    # nazione scritta in fondo (NED, ENG, nl…) o seed in fondo tra parentesi
    if len(out) >= 2:
        last = out[-1].strip("()[]")
        if last.isdigit() and seed is None and out[-1] != last:
            seed = int(last)
            out.pop()
        elif last.isalpha() and last.isupper() and 2 <= len(last) <= 3:
            code = _ALIASES.get(last, last)
            if code in KNOWN or len(last) == 3:
                nat = code
                out.pop()
    name = " ".join(out).strip()
    if not name:
        raise ParseError(f"nome mancante in «{text.strip()}»")
    return Player(name=name, seed=seed, nat=nat)


_SEP = re.compile(r"\s+(?:-|–|—|vs\.?|v)\s+", re.IGNORECASE)
_TITLE = re.compile(r"^\s*titolo\s*:\s*(.+)$", re.IGNORECASE)


def parse_draw(text: str) -> tuple[str | None, list[Player]]:
    """Testo incollato: (titolo facoltativo, giocatori in ordine di tabellone). Una partita per riga.

    Il numero di partite deve essere una potenza di 2 (2, 4, … 32, 64)."""
    title = None
    players: list[Player] = []
    errors = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        t = _TITLE.match(line)
        if t:
            title = t.group(1).strip()
            continue
        line = re.sub(r"^\s*\d+\s*[).:]\s+", "", line)  # numerazione "1) " / "1. " / "1: "
        parts = _SEP.split(line)
        if len(parts) != 2:
            errors.append(f"riga {n}: «{raw.strip()}»")
            continue
        try:
            players += [parse_player(parts[0]), parse_player(parts[1])]
        except ParseError as e:
            errors.append(f"riga {n}: {e}")
    if errors:
        raise ParseError("Non ho capito queste righe:\n" + "\n".join(errors))
    n = len(players) // 2
    if n < 2 or n & (n - 1) or n > 64:
        raise ParseError(f"Servono 2, 4, 8, 16, 32 o 64 partite del 1° turno (ne ho trovate {n}).")
    return title, players


def _find_result(p1: Player, p2: Player, matches) -> tuple[int, int | None, int | None] | None:
    """Cerca tra le partite salvate quella tra p1 e p2 (con vincitore). -> (vincitore 1/2, punti p1, punti p2)."""
    best, found = 0, None
    for m in matches:
        if m.winner not in (1, 2):
            continue
        straight = min(name_score(p1.name, m.player1), name_score(p2.name, m.player2))
        swapped = min(name_score(p1.name, m.player2), name_score(p2.name, m.player1))
        score = max(straight, swapped)
        if score == 0 or score < best:
            continue
        if straight >= swapped:
            w, s1, s2 = m.winner, m.score1, m.score2
        else:
            w, s1, s2 = 3 - m.winner, m.score2, m.score1
        if score > best or found is None:
            best, found = score, (w, s1, s2)
    return found


def build_rounds(players: list[Player], matches) -> list[list[BMatch]]:
    """Turni del tabellone: rounds[0] = 1° turno, rounds[-1] = finale."""
    cur = [BMatch(players[i], players[i + 1]) for i in range(0, len(players), 2)]
    rounds = [cur]
    while True:
        for bm in cur:
            if bm.p1 and bm.p2:
                r = _find_result(bm.p1, bm.p2, matches)
                if r:
                    bm.winner, bm.s1, bm.s2 = r
        if len(cur) == 1:
            break
        cur = [BMatch(cur[i].winner_player(), cur[i + 1].winner_player()) for i in range(0, len(cur), 2)]
        rounds.append(cur)
    return rounds


def round_names(n_rounds: int) -> list[str]:
    """Nomi dei turni dal primo alla finale."""
    tail = ["FINALE", "SEMIFINALI", "QUARTI", "OTTAVI"]
    names = []
    for i in range(n_rounds):
        from_end = n_rounds - 1 - i
        names.append(tail[from_end] if from_end < len(tail) else f"{i + 1}° TURNO")
    return names


def progress(rounds: list[list[BMatch]]) -> tuple[int, int]:
    """(partite con risultato, partite totali)."""
    allm = [m for r in rounds for m in r]
    return sum(m.winner is not None for m in allm), len(allm)


__all__ = ["Player", "BMatch", "parse_draw", "parse_player", "build_rounds", "round_names", "progress"]
