"""Parse the date shapes used on provider lifecycle pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}  # fmt: skip

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_LONG = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_FLOOR = re.compile(r"\b(?:not|no) (?:sooner|earlier) than\b|\bnot before\b", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedDate:
    """A date read from a page. `is_floor` marks "not sooner than" dates that are lower bounds."""

    value: date | None
    is_floor: bool = False


def _build(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _find(text: str) -> date | None:
    iso = _ISO.search(text)
    if iso:
        return _build(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    long_form = _LONG.search(text)
    if long_form:
        month = _MONTHS.get(long_form.group(1).lower())
        if month is not None:
            return _build(int(long_form.group(3)), month, int(long_form.group(2)))
    return None


def parse_date(text: str) -> ParsedDate:
    value = _find(text)
    return ParsedDate(value=value, is_floor=value is not None and bool(_FLOOR.search(text)))
