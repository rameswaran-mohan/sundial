"""One scan: collect every source, diff against the registry, persist records and events."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from sundial_llm.events import Event, diff
from sundial_llm.sources import Fetch, Source, collect
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
    old = store.load_records()
    result = collect(sources, fetch)
    baselined = store.baselined_sources()

    # A source seen for the first time is a baseline for its own records: it must not flood events.
    announced = {key for name, keys in result.keys_by_source.items() if name in baselined for key in keys}
    comparable = {key: record for key, record in result.records.items() if key in announced or key in old}
    events = diff(old, comparable)

    store.save_scan(result.records, events, now, sources_ok=result.keys_by_source)
    return ScanOutcome(
        total_records=len({**old, **result.records}),
        events=events,
        warnings=result.warnings,
        failures=result.failures,
        baseline=not old,
        sources_ok=tuple(result.keys_by_source),
    )
