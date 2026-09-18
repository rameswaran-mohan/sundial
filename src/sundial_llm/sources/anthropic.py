"""Anthropic model deprecations page: a status table plus dated history tables."""

from __future__ import annotations

from sundial_llm.dates import parse_date
from sundial_llm.mdtable import MODEL_ID, backticked, iter_tables
from sundial_llm.models import LifecycleRecord, LifecycleState, ParseResult, RecordKey, merge_records
from sundial_llm.sources._common import clean, finish

SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/model-deprecations.md"
PLATFORM = "anthropic"

_HISTORY_HEADER = ("retirement date", "deprecated model", "recommended replacement")
_STATES = {state.value: state for state in LifecycleState}


def _add(by_key: dict[RecordKey, LifecycleRecord], record: LifecycleRecord) -> None:
    existing = by_key.get(record.key)
    by_key[record.key] = record if existing is None else merge_records(existing, record)


def parse(text: str) -> ParseResult:
    by_key: dict[RecordKey, LifecycleRecord] = {}
    warnings: list[str] = []
    for table in iter_tables(text):
        header = tuple(cell.lower() for cell in table.header)
        if header and header[0] == "api model name" and len(header) >= 4:
            for row, raw in zip(table.rows, table.raw_rows):
                model_id = clean(row[0]).strip("`")
                state = _STATES.get(clean(row[1]).lower())
                if len(row) < 4 or state is None or not MODEL_ID.match(model_id):
                    warnings.append(f"anthropic: unreadable status row skipped: {raw}")
                    continue
                retirement = parse_date(row[3])
                _add(
                    by_key,
                    LifecycleRecord(
                        platform=PLATFORM,
                        model_id=model_id,
                        state=state,
                        deprecated_on=parse_date(row[2]).value,
                        retire_on=retirement.value,
                        retire_on_is_floor=retirement.is_floor,
                        source_url=SOURCE_URL,
                        evidence=raw,
                    ),
                )
        elif header == _HISTORY_HEADER:
            announced = parse_date(table.headings[-1]).value if table.headings else None
            for row, raw in zip(table.rows, table.raw_rows):
                retire_on = parse_date(row[0]).value
                for model_id in backticked(row[1]):
                    _add(
                        by_key,
                        LifecycleRecord(
                            platform=PLATFORM,
                            model_id=model_id,
                            state=LifecycleState.DEPRECATED,
                            announced_on=announced,
                            retire_on=retire_on,
                            replacements=backticked(row[2]),
                            source_url=SOURCE_URL,
                            evidence=raw,
                        ),
                    )
    return finish("anthropic", list(by_key.values()), warnings)
