"""Helpers shared by the source parsers."""

from __future__ import annotations

import re

from sundial_llm.models import LifecycleRecord, ParseResult, SourceFormatError

_FOOTNOTE = re.compile(r"<sup>.*?</sup>|\^\d+\^")
_EMPTY = frozenset({"", "—", "–", "-", "n/a"})


def clean(cell: str) -> str:
    """Cell text without footnote markers. Placeholder cells become an empty string."""
    text = _FOOTNOTE.sub("", cell).strip()
    return "" if text.lower() in _EMPTY else text


def finish(source: str, records: list[LifecycleRecord], warnings: list[str]) -> ParseResult:
    if not records:
        raise SourceFormatError(
            f"{source}: parsed zero lifecycle records; the page shape changed and the parser needs updating"
        )
    return ParseResult(records=tuple(records), warnings=tuple(warnings))
