from datetime import date, datetime, timezone

import pytest

from sundial_llm.events import Event, EventType
from sundial_llm.models import LifecycleRecord, LifecycleState
from sundial_llm.scan import run_scan
from sundial_llm.sources import ALL_SOURCES
from sundial_llm.store import Store

T1 = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)

FIXTURE_BY_SOURCE = {
    "openai-deprecations": "openai_deprecations.md",
    "openai-changelog": "openai_changelog.md",
    "anthropic": "anthropic_deprecations.md",
    "azure": "azure_retirement_schedule.md",
    "gemini": "gemini_deprecations.md",
    "bedrock": "bedrock_lifecycle_legacy.md",
}


def rec(model_id: str = "m", **extra: object) -> LifecycleRecord:
    base = {
        "platform": "openai",
        "model_id": model_id,
        "state": LifecycleState.ACTIVE,
        "source_url": "u",
        "evidence": "e",
    }
    return LifecycleRecord(**{**base, **extra})


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "sundial.db")


@pytest.fixture
def fixture_fetch(load_fixture):
    by_url = {source.url: FIXTURE_BY_SOURCE[source.name] for source in ALL_SOURCES}
    return lambda url: load_fixture(by_url[url])


def test_new_store_is_empty(store: Store) -> None:
    assert store.load_records() == {}
    assert store.last_scan_at() is None
    assert store.events() == []


def test_records_round_trip_with_every_field(store: Store) -> None:
    original = rec(
        state=LifecycleState.DEPRECATED,
        version="2024-05-13",
        retire_on=date(2026, 10, 1),
        retire_on_is_floor=True,
        replacements=("a", "b"),
    )

    store.save_scan({original.key: original}, (), T1)

    assert store.load_records() == {original.key: original}
    assert store.last_scan_at() == T1


def test_second_scan_keeps_first_seen_and_moves_last_seen(store: Store) -> None:
    store.save_scan({rec().key: rec()}, (), T1)
    store.save_scan({rec().key: rec()}, (), T2)

    assert store.seen(rec().key) == (T1, T2)


def test_record_missing_from_a_later_scan_is_kept(store: Store) -> None:
    store.save_scan({rec("old").key: rec("old")}, (), T1)
    store.save_scan({rec("new").key: rec("new")}, (), T2)

    assert set(store.load_records()) == {rec("old").key, rec("new").key}
    assert store.seen(rec("old").key) == (T1, T1)


def test_events_come_back_newest_first_with_their_time(store: Store) -> None:
    first = Event(type=EventType.MODEL_ADDED, key=rec("a").key, before=None, after=rec("a"))
    second = Event(type=EventType.MODEL_ADDED, key=rec("b").key, before=None, after=rec("b"))
    store.save_scan({}, (first,), T1)
    store.save_scan({}, (second,), T2)

    stored = store.events(limit=10)

    assert [(item.at, item.event.after.model_id) for item in stored] == [(T2, "b"), (T1, "a")]
    assert store.events(limit=1)[0].event == second


def test_first_scan_is_a_baseline_without_events(store: Store, fixture_fetch) -> None:
    outcome = run_scan(store, ALL_SOURCES, fixture_fetch, T1)

    assert outcome.baseline is True
    assert outcome.events == ()
    assert outcome.failures == {}
    assert len(store.load_records()) == outcome.total_records > 100


def test_second_scan_reports_and_persists_a_moved_retirement_date(store: Store, fixture_fetch) -> None:
    run_scan(store, ALL_SOURCES, fixture_fetch, T1)

    def moved(url: str) -> str:
        return fixture_fetch(url).replace(
            "| Oct 1, 2026   | `gpt-5.4-cyber`", "| Nov 1, 2026   | `gpt-5.4-cyber`"
        )

    outcome = run_scan(store, ALL_SOURCES, moved, T2)

    assert outcome.baseline is False
    assert [(e.type, e.key) for e in outcome.events] == [
        (EventType.RETIREMENT_DATE_CHANGED, ("openai", "gpt-5.4-cyber", ""))
    ]
    assert store.events()[0].event.after.retire_on == date(2026, 11, 1)
    assert store.load_records()[("openai", "gpt-5.4-cyber", "")].retire_on == date(2026, 11, 1)


def test_source_outage_neither_drops_records_nor_invents_events(store: Store, fixture_fetch) -> None:
    before = run_scan(store, ALL_SOURCES, fixture_fetch, T1)

    def outage(url: str) -> str:
        if "azure" in url:
            raise OSError("network down")
        return fixture_fetch(url)

    outcome = run_scan(store, ALL_SOURCES, outage, T2)

    assert outcome.events == ()
    assert "azure" in outcome.failures
    assert len(store.load_records()) == before.total_records


def test_source_that_was_down_during_the_baseline_joins_without_a_flood(store: Store, fixture_fetch) -> None:
    def outage(url: str) -> str:
        if "azure" in url:
            raise OSError("network down")
        return fixture_fetch(url)

    run_scan(store, ALL_SOURCES, outage, T1)
    outcome = run_scan(store, ALL_SOURCES, fixture_fetch, T2)

    assert outcome.events == ()
    assert any(key[0] == "azure" for key in store.load_records())
