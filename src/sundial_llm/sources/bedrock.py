"""Amazon Bedrock legacy-policy lifecycle page: one nested bullet block per model."""

from __future__ import annotations

import re

from sundial_llm.dates import parse_date
from sundial_llm.models import LifecycleRecord, LifecycleState, ParseResult, RecordKey
from sundial_llm.sources._common import finish

SOURCE_URL = "https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle-legacy.md"
PLATFORM = "bedrock"

_BLOCK_START = re.compile(r"^- \*\*(.+?)\*\*\s*$")
_FIELD = re.compile(r"\*\*([^*:]+):\*\*\s*(.*?)(?=\s*/\s*\*\*|$)")
_BEDROCK_ID = re.compile(r"^[a-z][a-z0-9-]+\.[a-z0-9][a-z0-9.\-]*(?::\d+)?$")


def _blocks(text: str) -> list[tuple[str, list[str]]]:
    blocks: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        start = _BLOCK_START.match(line)
        if start:
            blocks.append((start.group(1).strip(), []))
        elif blocks and line.startswith("  "):
            blocks[-1][1].append(line)
    return blocks


def _fields(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        for label, value in _FIELD.findall(line):
            fields.setdefault(label.strip().lower(), value.strip())
    return fields


def parse(text: str) -> ParseResult:
    by_key: dict[RecordKey, LifecycleRecord] = {}
    warnings: list[str] = []
    for vendor, lines in _blocks(text):
        fields = _fields(lines)
        labelled_id = fields.get("model id", "")
        shifted = not _BEDROCK_ID.match(labelled_id)
        model_id = (
            next((value for value in fields.values() if _BEDROCK_ID.match(value)), "")
            if shifted
            else labelled_id
        )
        if not model_id:
            continue
        legacy_on = parse_date(fields.get("legacy date", "")).value
        retire_on = parse_date(fields.get("eol date", "")).value
        if shifted or legacy_on is None or retire_on is None or retire_on <= legacy_on:
            warnings.append(
                f"bedrock: block for {model_id} has inconsistent labels or dates on the source page; skipped"
            )
            continue
        record = LifecycleRecord(
            platform=PLATFORM,
            model_id=model_id,
            vendor=vendor,
            state=LifecycleState.LEGACY,
            deprecated_on=legacy_on,
            retire_on=retire_on,
            source_url=SOURCE_URL,
            evidence=" | ".join(line.strip() for line in lines),
        )
        by_key.setdefault(record.key, record)
    return finish("bedrock", list(by_key.values()), warnings)
