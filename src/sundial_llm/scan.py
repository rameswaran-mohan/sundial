"""One scan: collect every source, diff against the registry, persist records and events."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sundial_llm.events import Event, diff
from sundial_llm.models import RecordKey
from sundial_llm.registry import RegistryDocument
from sundial_llm.sources import CollectResult, Fetch, Source, collect
from sundial_llm.store import Store


@dataclass(frozen=True)
class ScanOutcome:
    total_records: int
    events: tuple[Event, ...] = ()
    warnings: tuple[str, ...] = ()
    failures: dict[str, str] = field(default_factory=dict)
    baseline: bool = False
    sources_ok: tuple[str, ...] = ()


def run_scan(store: Store, sources: Iterable[Source], fetch: Fetch, now: datetime) -> ScanOutcome:
    return _record(store, collect(sources, fetch), now)


def run_import(store: Store, document: RegistryDocument, now: datetime) -> ScanOutcome:
    """Load a published registry document as if it were one source named `registry`.

    Each record keeps the first_seen and last_seen the document carries, not the import time.
    """
    records = document.lifecycle_records()
    seen = {stored.key: (stored.first_seen, stored.last_seen) for stored in document.records}
    result = CollectResult(records, keys_by_source={"registry": frozenset(records)})
    return _record(store, result, now, seen=seen, direct=False)


def _record(
    store: Store,
    result: CollectResult,
    now: datetime,
    seen: Mapping[RecordKey, tuple[datetime, datetime]] | None = None,
    direct: bool = True,
) -> ScanOutcome:
    old = store.load_records()
    baselined = store.baselined_sources()

    # A source seen for the first time is a baseline for its own records: it must not flood events.
    announced = {key for name, keys in result.keys_by_source.items() if name in baselined for key in keys}
    comparable = {key: record for key, record in result.records.items() if key in announced or key in old}
    events = diff(old, comparable)

    store.save_scan(
        result.records,
        events,
        now,
        sources_ok=result.keys_by_source,
        failures=result.failures,
        seen=seen,
        direct=direct,
    )
    return ScanOutcome(
        total_records=len({**old, **result.records}),
        events=events,
        warnings=result.warnings,
        failures=result.failures,
        baseline=not old,
        sources_ok=tuple(result.keys_by_source),
    )
