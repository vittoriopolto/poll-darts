import asyncio
from datetime import date
from types import SimpleNamespace as NS

from pdc import livescore as ls

# Testo vero di TheSportsDB (PC Finals 2026, giorno 1), accorciato.
TEXT = """Friday November 27\r
Round One\r
Afternoon Session (1300 GMT)\r
Main Stage\r
Martin Schindler 6-4 Charlie Manby\r
Kevin Doets 6-1 Ritchie Edhouse\r
\r
Stage Two\r
Rob Owen 6-5 Tom Bissell\r
James Hurrell 6-0 William O'Connor\r
\r
Evening Session (1900 GMT)\r
Ross Smith v Michael Smith\r
Michael van Gerwen v Connor Scutt"""


def M(i, p1, p2):
    return NS(id=i, player1=p1, player2=p2)


def test_parse_ignores_unplayed():
    rows = ls.parse_results(TEXT)
    assert [(a, sa, sb, b) for a, sa, sb, b, _ in rows][:2] == [
        ("Martin Schindler", 6, 4, "Charlie Manby"), ("Kevin Doets", 6, 1, "Ritchie Edhouse")]
    assert len(rows) == 4


def test_match_names_order_and_initials():
    matches = [
        M(1, "🏴 Bissell T.", "🏴󠁧󠁢󠁷󠁬󠁳󠁿 Owen R."),     # stile diretta.it, ordine invertito
        M(2, "Manby", "Schindler"),               # solo cognomi
        M(3, "🇮🇪 William O'Connor", "Hurrell"),
        M(4, "Michael Smith", "Ross Smith"),       # non ancora giocata
        M(5, "Gary Anderson", "Karel Sedlacek"),   # assente dal testo
    ]
    found = ls.match_results(matches, ls.parse_results(TEXT))
    assert (found[1].winner, found[1].score1, found[1].score2) == (2, 5, 6)
    assert (found[2].winner, found[2].score1, found[2].score2) == (2, 4, 6)
    assert (found[3].winner, found[3].score1, found[3].score2) == (2, 0, 6)
    assert 4 not in found and 5 not in found


def test_ambiguous_surname_is_skipped():
    rows = ls.parse_results("Ross Smith 6-4 Joe Cullen\nMichael Smith 6-2 Joe Cullen")
    assert ls.match_results([M(1, "Smith", "Cullen")], rows) == {}
    assert ls.match_results([M(1, "Michael Smith", "Cullen")], rows)[1].score1 == 6


def test_find_results_fetch_error(monkeypatch):
    async def boom(day):
        raise ls.FetchError("x")
    monkeypatch.setattr(ls, "fetch_day", boom)
    try:
        asyncio.run(ls.find_results([M(1, "a", "b")], [date(2026, 11, 27)]))
    except ls.FetchError:
        pass
    else:
        raise AssertionError


def test_find_results(monkeypatch):
    async def fake(day):
        return [TEXT]
    monkeypatch.setattr(ls, "fetch_day", fake)
    found = asyncio.run(ls.find_results([M(9, "Doets", "Edhouse")], [date(2026, 11, 27)]))
    assert found[9].winner == 1
