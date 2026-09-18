from datetime import date

from sundial_llm.events import EventType, diff
from sundial_llm.models import LifecycleRecord, LifecycleState


def rec(model_id: str = "m", state: LifecycleState = LifecycleState.ACTIVE, **extra: object) -> LifecycleRecord:
    return LifecycleRecord(platform="openai", model_id=model_id, state=state, source_url="u", evidence="e", **extra)


def index(*records: LifecycleRecord) -> dict:
    return {r.key: r for r in records}


def types(events) -> list[EventType]:
    return [event.type for event in events]


def test_unseen_active_model_is_a_model_added_event() -> None:
    events = diff(index(), index(rec()))

    assert types(events) == [EventType.MODEL_ADDED]
    assert events[0].before is None
    assert events[0].after.model_id == "m"


def test_unseen_model_that_is_already_deprecated_is_an_announcement() -> None:
    events = diff(index(), index(rec(state=LifecycleState.DEPRECATED, retire_on=date(2026, 1, 1))))

    assert types(events) == [EventType.DEPRECATION_ANNOUNCED]


def test_unseen_model_that_is_already_retired_is_not_news() -> None:
    assert diff(index(), index(rec(state=LifecycleState.RETIRED))) == ()


def test_active_to_deprecated_is_one_announcement_not_three_events() -> None:
    before = rec()
    after = rec(state=LifecycleState.DEPRECATED, retire_on=date(2026, 1, 1), replacements=("n",))

    events = diff(index(before), index(after))

    assert types(events) == [EventType.DEPRECATION_ANNOUNCED]
    assert events[0].before == before


def test_moved_retirement_date_is_reported() -> None:
    before = rec(state=LifecycleState.DEPRECATED, retire_on=date(2026, 1, 1))
    after = rec(state=LifecycleState.DEPRECATED, retire_on=date(2026, 3, 1))

    assert types(diff(index(before), index(after))) == [EventType.RETIREMENT_DATE_CHANGED]


def test_replacement_appearing_later_is_reported() -> None:
    before = rec(state=LifecycleState.DEPRECATED, retire_on=date(2026, 1, 1))
    after = rec(state=LifecycleState.DEPRECATED, retire_on=date(2026, 1, 1), replacements=("n",))

    assert types(diff(index(before), index(after))) == [EventType.REPLACEMENT_NAMED]


def test_state_reaching_retired_is_a_retirement() -> None:
    before = rec(state=LifecycleState.DEPRECATED)
    after = rec(state=LifecycleState.RETIRED)

    assert types(diff(index(before), index(after))) == [EventType.MODEL_RETIRED]


def test_other_state_moves_are_generic_state_changes() -> None:
    before = rec(state=LifecycleState.PREVIEW)
    after = rec(state=LifecycleState.ACTIVE)

    assert types(diff(index(before), index(after))) == [EventType.STATE_CHANGED]


def test_identical_scans_and_vanished_models_produce_nothing() -> None:
    assert diff(index(rec()), index(rec())) == ()
    assert diff(index(rec()), index()) == ()


def test_events_come_out_in_key_order() -> None:
    events = diff(index(), index(rec("zeta"), rec("alpha")))

    assert [event.after.model_id for event in events] == ["alpha", "zeta"]
