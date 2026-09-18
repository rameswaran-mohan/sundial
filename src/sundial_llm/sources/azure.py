"""Microsoft Foundry model retirement schedule, read from the public docs repository."""

from __future__ import annotations

from sundial_llm.dates import parse_date
from sundial_llm.mdtable import iter_tables
from sundial_llm.models import LifecycleRecord, LifecycleState, ParseResult, RecordKey
from sundial_llm.sources._common import clean, finish

# The published page is what customers see. It serves markdown when asked for it.
SOURCE_URL = "https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule"
# The docs repository can run ahead of publication, so it is only the fallback.
FALLBACK_URL = (
    "https://raw.githubusercontent.com/MicrosoftDocs/azure-ai-docs/main/"
    "articles/foundry/openai/includes/concepts-model-retirement-schedule-content.md"
)
PLATFORM = "azure"

_HEADER = ("model", "version", "lifecycle", "retirement date", "replacement")
_LIFECYCLE = {
    "preview": LifecycleState.PREVIEW,
    "ga": LifecycleState.ACTIVE,
    "generally available": LifecycleState.ACTIVE,
    "legacy": LifecycleState.LEGACY,
    "deprecated": LifecycleState.DEPRECATED,
    "retired": LifecycleState.RETIRED,
}


def _earlier(a: LifecycleRecord, b: LifecycleRecord) -> LifecycleRecord:
    if a.retire_on is None:
        return b
    if b.retire_on is None:
        return a
    return a if a.retire_on <= b.retire_on else b


def parse(text: str) -> ParseResult:
    by_key: dict[RecordKey, LifecycleRecord] = {}
    warnings: list[str] = []
    for table in iter_tables(text):
        if tuple(cell.lower() for cell in table.header) != _HEADER:
            continue
        vendor = table.headings[-1] if table.headings else None
        for row, raw in zip(table.rows, table.raw_rows, strict=True):
            if len(row) != len(_HEADER):
                continue
            model_id = clean(row[0]).split(" (")[0].strip()
            state = _LIFECYCLE.get(clean(row[2]).lower())
            if not model_id or state is None:
                warnings.append(f"azure: unreadable row skipped: {raw}")
                continue
            record = LifecycleRecord(
                platform=PLATFORM,
                model_id=model_id,
                version=clean(row[1]) or None,
                vendor=vendor,
                state=state,
                retire_on=parse_date(clean(row[3])).value,
                replacements=tuple(part.strip() for part in clean(row[4]).split(",") if part.strip()),
                source_url=SOURCE_URL,
                evidence=raw,
            )
            existing = by_key.get(record.key)
            if existing is not None:
                warnings.append(
                    f"azure: {model_id} version {record.version} is listed more than once; "
                    "keeping the earliest retirement date"
                )
                conflicting = existing.retire_on != record.retire_on
                record = _earlier(existing, record)
                if conflicting or existing.ambiguous:
                    record = record.model_copy(update={"ambiguous": True})
            by_key[record.key] = record
    return finish("azure", list(by_key.values()), warnings)
