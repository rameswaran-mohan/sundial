"""Gemini API deprecations page: one table per model family.

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


def _column(header: tuple[str, ...], name: str) -> int | None:
    return header.index(name) if name in header else None


def parse(text: str) -> ParseResult:
    by_key: dict[RecordKey, LifecycleRecord] = {}
    for table in iter_tables(text):
        header = tuple(cell.lower() for cell in table.header)
        shutdown_col = _column(header, "shutdown date")
        if not header or header[0] != "model" or shutdown_col is None:
            continue
        release_col = _column(header, "release date")
        replacement_col = _column(header, "recommended replacement")
        vendor = table.headings[-1] if table.headings else None
        preview = False
        for row, raw in zip(table.rows, table.raw_rows):
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
                replacements=backticked(row[replacement_col]) if has_replacement else (),
                source_url=SOURCE_URL,
                evidence=raw,
            )
            by_key.setdefault(record.key, record)
    return finish("gemini", list(by_key.values()), [])
