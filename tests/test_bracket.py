from types import SimpleNamespace as NS

import pytest

from pdc import bracket, images
from pdc.parsing import ParseError


def test_parse_player():
    p = bracket.parse_player("1 Wessel Nijman NED")
    assert (p.seed, p.name, p.nat) == (1, "Wessel Nijman", "NED")
    p = bracket.parse_player("(12) 🇮🇪 William O'Connor")
    assert (p.seed, p.name, p.nat) == (12, "William O'Connor", "IRL")
    p = bracket.parse_player("🏴󠁧󠁢󠁳󠁣󠁴󠁿 Gary Anderson")
    assert (p.seed, p.name, p.nat) == (None, "Gary Anderson", "SCO")
    p = bracket.parse_player("Nijman")
    assert (p.seed, p.name, p.nat) == (None, "Nijman", None)
    p = bracket.parse_player("Jeffrey de Graaf SWE")
    assert p.name == "Jeffrey de Graaf" and p.nat == "SWE"


def test_parse_draw_errors():
    with pytest.raises(ParseError):
        bracket.parse_draw("A - B\nC - D\nE - F")
    with pytest.raises(ParseError):
        bracket.parse_draw("solo una riga senza separatore")
    title, players = bracket.parse_draw("Titolo: Test\n1) A - B\n2) C - D")
    assert title == "Test" and [p.name for p in players] == ["A", "B", "C", "D"]


def test_build_rounds_and_image():
    _, players = bracket.parse_draw("Ross Smith - Michael Smith\nNijman - Coates\nClemens - Chisnall\nManby - Schindler")
    ms = [NS(player1="Smith M.", player2="Smith R.", winner=1, score1=6, score2=3),
          NS(player1="🇳🇱 Nijman", player2="Coates", winner=1, score1=6, score2=2),
          NS(player1="Schindler", player2="Manby", winner=1, score1=6, score2=4),
          NS(player1="Nijman", player2="Michael Smith", winner=2, score1=1, score2=6),
          NS(player1="Clemens", player2="Chisnall", winner=None, score1=None, score2=None)]
    rounds = bracket.build_rounds(players, ms)
    r1 = rounds[0]
    assert (r1[0].winner, r1[0].s1, r1[0].s2) == (2, 3, 6)       # invertito: vince Michael Smith
    assert (r1[1].winner, r1[2].winner, r1[3].winner) == (1, None, 2)
    assert rounds[1][0].p1.name == "Michael Smith" and rounds[1][0].winner == 1
    assert rounds[1][1].p1 is None and rounds[1][1].p2.name == "Schindler"
    assert bracket.progress(rounds) == (4, 7)
    assert bracket.round_names(6) == ["1° TURNO", "2° TURNO", "OTTAVI", "QUARTI", "SEMIFINALI", "FINALE"]
    png = images.bracket_image("Players Championship Finals", "TABELLONE", rounds, bracket.round_names(3), "x")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_special_letters_match():
    _, players = bracket.parse_draw("Sebastian Białecki POL - Gerwyn Price WAL\nA - B")
    ms = [NS(player1="🇵🇱 Bialecki", player2="Price", winner=1, score1=6, score2=4)]
    rounds = bracket.build_rounds(players, ms)
    assert (rounds[0][0].winner, rounds[0][0].s1) == (1, 6)
