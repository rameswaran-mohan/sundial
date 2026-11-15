"""Gemini API deprecations page: tables grouped by model family.

The page has been served in two shapes. Until 2026-11-04 it had one pipe table per family, with a
`Preview models` divider row before the preview rows. Since then it has Markdoc tables, with preview
and deprecated rows in their own tables under `### Preview models` / `### Deprecated models`
subheadings of the family heading. Both shapes are read.

Google labels shutdown dates as the earliest possible date. They are announced retirements, so they
are recorded as firm dates: warning early is the safe reading.
"""

from __future__ import annotations

from sundial_llm.dates import parse_date
from sundial_llm.mdtable import backticked, iter_tables
from sundial_llm.models import LifecycleRecord, LifecycleState, ParseResult, RecordKey
from sundial_llm.sources._common import finish

SOURCE_URL = "https://ai.google.dev/gemini-api/docs/deprecations.md.txt"
PLATFORM = "gemini"


# Subheadings that group a family's rows by status rather than name a family.
_STATUS_SUBHEADINGS = ("preview", "deprecated")


def _column(header: tuple[str, ...], name: str) -> int | None:
    return header.index(name) if name in header else None


def _family_and_status(headings: tuple[str, ...]) -> tuple[str | None, str]:
    """The family heading a table sits under, and its status subheading ("" when it has none)."""
    if not headings:
        return None, ""
    last = headings[-1]
    if len(headings) > 1 and last.lower().startswith(_STATUS_SUBHEADINGS):
        return headings[-2], last
    return last, ""


def parse(text: str) -> ParseResult:
    by_key: dict[RecordKey, LifecycleRecord] = {}
    for table in iter_tables(text):
        header = tuple(cell.lower() for cell in table.header)
        shutdown_col = _column(header, "shutdown date")
        if not header or header[0] != "model" or shutdown_col is None:
            continue
        release_col = _column(header, "release date")
        replacement_col = _column(header, "recommended replacement")
        vendor, status = _family_and_status(table.headings)
        preview = status.lower().startswith("preview")
        for row, raw in zip(table.rows, table.raw_rows, strict=True):
            if row and row[0].lower().startswith("preview models"):
                preview = True
                continue
            model_ids = backticked(row[0]) if row else ()
            if not model_ids or len(row) <= shutdown_col:
                continue
            retire_on = parse_date(row[shutdown_col]).value
            if retire_on is not None:
                state = LifecycleState.DEPRECATED
            else:
                state = LifecycleState.PREVIEW if preview else LifecycleState.ACTIVE
            has_replacement = replacement_col is not None and len(row) > replacement_col
            # Routing notes in the cell name the model itself; it is never its own replacement.
            replacements = backticked(row[replacement_col]) if has_replacement else ()
            replacements = tuple(r for r in replacements if r != model_ids[0])
            record = LifecycleRecord(
                platform=PLATFORM,
                model_id=model_ids[0],
                vendor=vendor,
                state=state,
                released_on=(
                    parse_date(row[release_col]).value
                    if release_col is not None and len(row) > release_col
                    else None
                ),
                retire_on=retire_on,
                replacements=replacements,
                source_url=SOURCE_URL,
                evidence=raw,
            )
            by_key.setdefault(record.key, record)
    return finish("gemini", list(by_key.values()), [])
