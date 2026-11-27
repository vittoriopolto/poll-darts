from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pdc.parsing import ParseError, competition_positions, parse_matches, parse_start

TZ = ZoneInfo("Europe/Rome")


def test_parse_matches_formats():
    text = """
    1) Rossi - Bianchi
    2. Van Gerwen vs Littler
    - Humphries – Price
    Smith-Wright
    """
    assert parse_matches(text) == [
        ("Rossi", "Bianchi"), ("Van Gerwen", "Littler"), ("Humphries", "Price"), ("Smith", "Wright")]


def test_parse_matches_hyphenated_names():
    assert parse_matches("Jean-Luc Rossi - Anna Maria") == [("Jean-Luc Rossi", "Anna Maria")]


def test_parse_matches_errors():
    with pytest.raises(ParseError):
        parse_matches("solo un nome")
    with pytest.raises(ParseError):
        parse_matches("Rossi - rossi")
    with pytest.raises(ParseError):
        parse_matches("   \n  ")


def test_parse_start():
    now = datetime(2026, 11, 24, 16, 0, tzinfo=TZ)
    assert parse_start("25/11/2026 15:30", TZ, now) == datetime(2026, 11, 25, 15, 30, tzinfo=TZ)
    assert parse_start("25/11 15.30", TZ, now) == datetime(2026, 11, 25, 15, 30, tzinfo=TZ)
    assert parse_start("domani 9:05", TZ, now) == datetime(2026, 11, 25, 9, 5, tzinfo=TZ)
    assert parse_start("18:00", TZ, now) == datetime(2026, 11, 24, 18, 0, tzinfo=TZ)
    assert parse_start("15:00", TZ, now) == datetime(2026, 11, 25, 15, 0, tzinfo=TZ)  # già passato -> domani
    with pytest.raises(ParseError):
        parse_start("boh", TZ, now)
    with pytest.raises(ParseError):
        parse_start("31/02/2026 10:00", TZ, now)


def test_positions():
    assert competition_positions([5, 5, 3, 2, 2, 0]) == [1, 1, 3, 4, 4, 6]
    assert competition_positions([]) == []


def test_image_chunks_even():
    from pdc.images import chunks
    sizes = lambda n: [len(c) for c in chunks(list(range(n)), 30)]
    assert sizes(0) == [0] and sizes(1) == [1] and sizes(30) == [30]
    assert sizes(31) == [16, 15] and sizes(50) == [25, 25] and sizes(60) == [30, 30]
    assert sizes(80) == [27, 27, 26] and sizes(91) == [23, 23, 23, 22]
    assert sum(chunks(list(range(80)), 30), []) == list(range(80))
