"""The published registry: one JSON document carrying every lifecycle record and where it came from."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from sundial_llm.models import LifecycleRecord, RecordKey
from sundial_llm.store import Store

SCHEMA_VERSION = 1

_TIMESTAMPS = frozenset({"first_seen", "last_seen"})


class StoredRecord(LifecycleRecord):
    """A lifecycle record with the times the registry first and last saw it."""

    first_seen: datetime
    last_seen: datetime

    def lifecycle_record(self) -> LifecycleRecord:
        return LifecycleRecord(**self.model_dump(exclude=_TIMESTAMPS))


class RegistryDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = SCHEMA_VERSION
    generated_at: datetime
    sources: dict[str, str]  # source name -> "ok" or the failure text
    records: tuple[StoredRecord, ...]

    @field_validator("sources")
    @classmethod
    def _sort_sources(cls, sources: dict[str, str]) -> dict[str, str]:
        return dict(sorted(sources.items()))

    @field_validator("records")
    @classmethod
    def _sort_records(cls, records: tuple[StoredRecord, ...]) -> tuple[StoredRecord, ...]:
        return tuple(sorted(records, key=lambda record: record.key))

    def lifecycle_records(self) -> dict[RecordKey, LifecycleRecord]:
        """The records by key, without the times the registry saw them."""
        return {stored.key: stored.lifecycle_record() for stored in self.records}


def build_document(
    store: Store, now: datetime, sources_ok: Iterable[str], failures: Mapping[str, str]
) -> RegistryDocument:
    records = store.load_records()
    seen = store.seen_all()
    stored = tuple(
        StoredRecord(**record.model_dump(), first_seen=seen[key][0], last_seen=seen[key][1])
        for key, record in records.items()
    )
    sources = {**dict.fromkeys(sources_ok, "ok"), **failures}
    return RegistryDocument(generated_at=now, sources=sources, records=stored)


def to_json(document: RegistryDocument) -> str:
    return json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def parse_document(text: str) -> RegistryDocument:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"registry document is not JSON: {error}") from error
    if not isinstance(data, dict) or "schema_version" not in data:
        raise ValueError("registry document has no schema_version")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError(
            f"registry schema_version {data['schema_version']!r} is not supported (expected {SCHEMA_VERSION})"
        )
    try:
        return RegistryDocument.model_validate(data)
    except ValidationError as error:
        raise ValueError(f"invalid registry document: {error}") from error


def write_if_changed(document: RegistryDocument, path: Path) -> bool:
    """Write the document unless the file already holds the same lifecycle facts. True if written.

    Timestamps (`generated_at`, `first_seen`, `last_seen`) and source outcomes are ignored, so the
    file only changes when a provider changed something.
    """
    try:
        existing = parse_document(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = None
    if existing is not None and _lifecycle(existing) == _lifecycle(document):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(document), encoding="utf-8", newline="\n")
    return True


def _lifecycle(document: RegistryDocument) -> tuple[LifecycleRecord, ...]:
    return tuple(record.lifecycle_record() for record in document.records)
