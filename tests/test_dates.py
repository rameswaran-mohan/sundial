from datetime import date

import pytest

from sundial_llm.dates import parse_date


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-01", date(2026, 10, 1)),
        ("Oct 1, 2026", date(2026, 10, 1)),
        ("October 23, 2026", date(2026, 10, 23)),
        ("May 8th, 2026", date(2026, 5, 8)),
        ("  September 15, 2026 ", date(2026, 9, 15)),
        ("Sept 3, 2026", date(2026, 9, 3)),
    ],
)
def test_parses_every_date_shape_the_providers_use(text: str, expected: date) -> None:
    parsed = parse_date(text)

    assert parsed.value == expected
    assert parsed.is_floor is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Not sooner than September 1, 2027", date(2027, 9, 1)),
        ("No earlier than 2027-04-01^1^", date(2027, 4, 1)),
    ],
)
def test_marks_not_sooner_than_dates_as_floors(text: str, expected: date) -> None:
    parsed = parse_date(text)

    assert parsed.value == expected
    assert parsed.is_floor is True


@pytest.mark.parametrize("text", ["N/A", "—", "-", "", "   ", "No shutdown date announced"])
def test_placeholders_mean_no_date(text: str) -> None:
    parsed = parse_date(text)

    assert parsed.value is None
    assert parsed.is_floor is False


@pytest.mark.parametrize("text", ["soon", "February 30, 2026", "2026-13-40"])
def test_unparseable_text_returns_no_date_instead_of_raising(text: str) -> None:
    assert parse_date(text).value is None
