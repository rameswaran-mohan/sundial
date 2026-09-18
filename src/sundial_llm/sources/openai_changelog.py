"""OpenAI changelog: the only release-1 source where new models are not in a lifecycle table."""

from __future__ import annotations

import re

from sundial_llm.dates import parse_date
from sundial_llm.mdtable import MODEL_ID
from sundial_llm.models import LifecycleRecord, LifecycleState, ParseResult, RecordKey
from sundial_llm.sources._common import finish

SOURCE_URL = "https://developers.openai.com/api/docs/changelog.md"
PLATFORM = "openai"

_MONTH_HEADING = re.compile(r"^##\s+[A-Za-z]+,?\s+(\d{4})\s*$")
_DAY_HEADING = re.compile(r"^###\s+([A-Za-z]{3,9})\.?\s+(\d{1,2})\s*$")
_MODEL = re.compile(r"Model:\s*([^\s·]+)")


def parse(text: str) -> ParseResult:
    by_key: dict[RecordKey, LifecycleRecord] = {}
    year: str | None = None
    day = None
    for line in text.splitlines():
        month_heading = _MONTH_HEADING.match(line)
        if month_heading:
            year = month_heading.group(1)
            continue
        day_heading = _DAY_HEADING.match(line)
        if day_heading and year:
            day = parse_date(f"{day_heading.group(1)} {day_heading.group(2)}, {year}").value
            continue
        if day is None or not line.startswith("Feature"):
            continue
        for model_id in _MODEL.findall(line):
            if not MODEL_ID.match(model_id):
                continue
            record = LifecycleRecord(
                platform=PLATFORM,
                model_id=model_id,
                state=LifecycleState.ACTIVE,
                released_on=day,
                source_url=SOURCE_URL,
                evidence=line.strip(),
            )
            existing = by_key.get(record.key)
            if existing is None or (existing.released_on and day < existing.released_on):
                by_key[record.key] = record
    return finish("openai changelog", list(by_key.values()), [])
