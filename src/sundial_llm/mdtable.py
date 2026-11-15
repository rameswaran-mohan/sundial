"""Read tables out of markdown, keeping the heading path each table sits under.

Two table shapes are read: GitHub pipe tables and Markdoc `{% table %}` blocks, where each row is a
list of `*`, `-` or `+` items and rows are separated by `---` lines. The first Markdoc row is the
header.
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
_MARKDOC_OPEN = re.compile(r"^\s*\{%\s*table\b.*%\}\s*$")
_MARKDOC_CLOSE = re.compile(r"^\s*\{%\s*/table\s*%\}\s*$")
_MARKDOC_TAG = re.compile(r"^\s*\{%")
# `---` or `- - -` breaks a row. It is matched before items, so it never reads as a `-` item.
_MARKDOC_ROW_BREAK = re.compile(r"^(\s*)-(?:[ \t]*-){2,}\s*$")
_MARKDOC_ITEM = re.compile(r"^(\s*)[*+-](?:\s+(.*?))?\s*$")


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


def _is_closed(lines: list[str], index: int) -> bool:
    """Whether the Markdoc table opening at `index` has its closing tag before the next table or heading."""
    for line in lines[index + 1 :]:
        if _MARKDOC_CLOSE.match(line):
            return True
        if _MARKDOC_OPEN.match(line) or _HEADING.match(line):
            return False
    return False


def _ends_unclosed_table(line: str, indent: int) -> bool:
    """Whether `line` lies past an unclosed table whose items sit at `indent`: a tag, or prose no deeper."""
    if _MARKDOC_TAG.match(line):
        return True
    is_prose = bool(line.strip()) and not (_MARKDOC_ITEM.match(line) or _MARKDOC_ROW_BREAK.match(line))
    return is_prose and len(line) - len(line.lstrip()) <= indent


def _markdoc_rows(lines: list[str], index: int) -> tuple[list[tuple[str, ...]], int]:
    """Rows of the Markdoc table opening at `index`, and the index of the first line after it.

    An item at the indent of the table's first item starts a cell; deeper or unmarked lines continue
    the current cell. Without a closing tag, the table ends at the next heading, any other tag line,
    or prose no deeper than its items, so text after it is not folded into the last cell.
    """
    rows: list[tuple[str, ...]] = []
    cells: list[str] = []
    indent: int | None = None
    closed = _is_closed(lines, index)
    index += 1
    while index < len(lines):
        line = lines[index]
        if _MARKDOC_CLOSE.match(line):
            index += 1
            break
        if _HEADING.match(line) or (not closed and _ends_unclosed_table(line, indent or 0)):
            break
        index += 1
        rule = _MARKDOC_ROW_BREAK.match(line)
        if rule:
            # A rule deeper than the items sits inside a cell and adds no text to it.
            if indent is None or len(rule.group(1)) <= indent:
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
