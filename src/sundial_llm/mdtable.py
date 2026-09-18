"""Read pipe tables out of markdown, keeping the heading path each table sits under."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]*$")

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_BOLD = re.compile(r"\*\*(.*?)\*\*")
_BACKTICKED = re.compile(r"`([^`]+)`")


@dataclass(frozen=True)
class Table:
    headings: tuple[str, ...]
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    raw_rows: tuple[str, ...]


def _cells(line: str) -> tuple[str, ...]:
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return tuple(cell.strip().replace("\\|", "|") for cell in _CELL_SPLIT.split(inner))


def _is_separator(line: str) -> bool:
    cells = _cells(line)
    return bool(cells) and all(_SEPARATOR_CELL.match(cell) for cell in cells)


def iter_tables(markdown: str) -> Iterator[Table]:
    lines = markdown.splitlines()
    path: dict[int, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            path = {lvl: text for lvl, text in path.items() if lvl < level}
            path[level] = heading.group(2)
            index += 1
            continue
        is_header = (
            line.lstrip().startswith("|") and index + 1 < len(lines) and _is_separator(lines[index + 1])
        )
        if not is_header:
            index += 1
            continue
        header = tuple(_BOLD.sub(r"\1", cell) for cell in _cells(line))
        index += 2
        rows: list[tuple[str, ...]] = []
        raw_rows: list[str] = []
        while index < len(lines) and lines[index].lstrip().startswith("|"):
            rows.append(_cells(lines[index]))
            raw_rows.append(lines[index].strip())
            index += 1
        yield Table(
            headings=tuple(path[lvl] for lvl in sorted(path)),
            header=header,
            rows=tuple(rows),
            raw_rows=tuple(raw_rows),
        )


def backticked(text: str) -> tuple[str, ...]:
    """Backticked tokens that look like model ids, in order, without duplicates."""
    found: list[str] = []
    for token in _BACKTICKED.findall(text):
        candidate = token.strip()
        if MODEL_ID.match(candidate) and candidate not in found:
            found.append(candidate)
    return tuple(found)
