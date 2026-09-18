"""Turn the difference between two scans into events. Rules and notifications consume only these."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from pydantic import BaseModel, ConfigDict

from sundial_llm.models import LifecycleRecord, LifecycleState, RecordKey

_WINDING_DOWN = (LifecycleState.LEGACY, LifecycleState.DEPRECATED)


class EventType(str, Enum):
    MODEL_ADDED = "model_added"
    DEPRECATION_ANNOUNCED = "deprecation_announced"
    RETIREMENT_DATE_CHANGED = "retirement_date_changed"
    REPLACEMENT_NAMED = "replacement_named"
    MODEL_RETIRED = "model_retired"
    STATE_CHANGED = "state_changed"


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: EventType
    key: RecordKey
    before: LifecycleRecord | None
    after: LifecycleRecord


def _for_new(after: LifecycleRecord) -> EventType | None:
    if after.state is LifecycleState.RETIRED:
        return None
    return EventType.DEPRECATION_ANNOUNCED if after.state in _WINDING_DOWN else EventType.MODEL_ADDED


def _for_changed(before: LifecycleRecord, after: LifecycleRecord) -> EventType | None:
    if after.state is not before.state:
        if after.state is LifecycleState.RETIRED:
            return EventType.MODEL_RETIRED
        if after.state in _WINDING_DOWN and after.state.severity > before.state.severity:
            return EventType.DEPRECATION_ANNOUNCED
        return EventType.STATE_CHANGED
    if after.retire_on != before.retire_on:
        return EventType.RETIREMENT_DATE_CHANGED
    if after.replacements and not before.replacements:
        return EventType.REPLACEMENT_NAMED
    return None


def diff(
    old: Mapping[RecordKey, LifecycleRecord], new: Mapping[RecordKey, LifecycleRecord]
) -> tuple[Event, ...]:
    """One event per changed model. Models missing from `new` produce nothing: records are never dropped."""
    events: list[Event] = []
    for key in sorted(new):
        after = new[key]
        before = old.get(key)
        event_type = _for_new(after) if before is None else _for_changed(before, after)
        if event_type is not None:
            events.append(Event(type=event_type, key=key, before=before, after=after))
    return tuple(events)
