"""Read tables out of markdown, keeping the heading path each table sits under.

Two table shapes are read: GitHub pipe tables and Markdoc `{% table %}` blocks, where each row is a
list of `*` items and rows are separated by `---` lines. The first Markdoc row is the header.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]*$")

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
# Markdoc annotations such as `{% id="gemini-2.0-models" %}` at the end of a heading.
_HEADING_TAGS = re.compile(r"(?:\s*\{%[^%]*%\})+\s*$")
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_BOLD = re.compile(r"\*\*(.*?)\*\*")
_BACKTICKED = re.compile(r"`([^`]+)`")
_MARKDOC_OPEN = re.compile(r"^\s*\{%\s*table\b[^%]*%\}\s*$")
_MARKDOC_CLOSE = re.compile(r"^\s*\{%\s*/table\s*%\}\s*$")
_MARKDOC_ROW_BREAK = re.compile(r"^\s*-{3,}\s*$")
_MARKDOC_ITEM = re.compile(r"^(\s*)\*(?:\s+(.*?))?\s*$")


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


def _markdoc_rows(lines: list[str], index: int) -> tuple[list[tuple[str, ...]], int]:
    """Rows of the Markdoc table opening at `index`, and the index of the first line after it.

    An item at the indent of the table's first item starts a cell; deeper or unmarked lines continue
    the current cell. A missing closing tag ends the table at the next heading.
    """
    rows: list[tuple[str, ...]] = []
    cells: list[str] = []
    indent: int | None = None
    index += 1
    while index < len(lines):
        line = lines[index]
        if _MARKDOC_CLOSE.match(line):
            index += 1
            break
        if _HEADING.match(line):
            break
        index += 1
        if _MARKDOC_ROW_BREAK.match(line):
            if cells:
                rows.append(tuple(cells))
            cells = []
            continue
        item = _MARKDOC_ITEM.match(line)
        if item and (indent is None or len(item.group(1)) <= indent):
            indent = len(item.group(1)) if indent is None else indent
            cells.append(item.group(2) or "")
        elif cells and line.strip():
            cells[-1] = f"{cells[-1]} {line.strip()}".strip()
    if cells:
        rows.append(tuple(cells))
    return rows, index


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
            path[level] = _HEADING_TAGS.sub("", heading.group(2))
            index += 1
            continue
        if _MARKDOC_OPEN.match(line):
            markdoc, index = _markdoc_rows(lines, index)
            if markdoc:
                yield Table(
                    headings=tuple(path[lvl] for lvl in sorted(path)),
                    header=tuple(_BOLD.sub(r"\1", cell) for cell in markdoc[0]),
                    rows=tuple(markdoc[1:]),
                    raw_rows=tuple(f"| {' | '.join(row)} |" for row in markdoc[1:]),
                )
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
