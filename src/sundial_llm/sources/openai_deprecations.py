"""OpenAI deprecations page: dated announcement sections, each with shutdown tables."""

from __future__ import annotations

from sundial_llm.dates import parse_date
from sundial_llm.mdtable import backticked, iter_tables
from sundial_llm.models import LifecycleRecord, LifecycleState, ParseResult, RecordKey
from sundial_llm.sources._common import finish

SOURCE_URL = "https://developers.openai.com/api/docs/deprecations.md"
PLATFORM = "openai"


def parse(text: str) -> ParseResult:
    by_key: dict[RecordKey, LifecycleRecord] = {}
    warnings: list[str] = []
    for table in iter_tables(text):
        header = tuple(cell.lower() for cell in table.header)
        if len(header) < 3 or not header[0].startswith("shutdown date") or header[1] == "system":
            continue
        announced = parse_date(table.headings[-1]).value if table.headings else None
        for row, raw in zip(table.rows, table.raw_rows):
            if len(row) < 3:
                continue
            model_ids = backticked(row[1])
            if not model_ids:
                continue
            retire_on = parse_date(row[0]).value
            if retire_on is None:
                warnings.append(f"openai: shutdown date not understood, row skipped: {raw}")
                continue
            for model_id in model_ids:
                record = LifecycleRecord(
                    platform=PLATFORM,
                    model_id=model_id,
                    state=LifecycleState.DEPRECATED,
                    announced_on=announced,
                    retire_on=retire_on,
                    replacements=backticked(row[-1]),
                    source_url=SOURCE_URL,
                    evidence=raw,
                )
                by_key.setdefault(record.key, record)
    return finish("openai deprecations", list(by_key.values()), warnings)
