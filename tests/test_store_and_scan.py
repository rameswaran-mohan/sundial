from datetime import date, datetime, timedelta, timezone

import pytest

from sundial_llm.events import Event, EventType
from sundial_llm.models import LifecycleRecord, LifecycleState
from sundial_llm.registry import RegistryDocument, StoredRecord, build_document
from sundial_llm.scan import run_import, run_scan
from sundial_llm.sources import ALL_SOURCES
from sundial_llm.store import Store

T0 = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
T3 = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
T4 = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)
# One hour before T2, written in an offset whose ISO text sorts after T2's.
BEFORE_T2_IN_PLUS_5 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone(timedelta(hours=5)))

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


def stored(
    model_id: str, first_seen: datetime = T1, last_seen: datetime = T1, **extra: object
) -> StoredRecord:
    return StoredRecord(**rec(model_id, **extra).model_dump(), first_seen=first_seen, last_seen=last_seen)


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "sundial.db")


@pytest.fixture
def fixture_fetch(load_fixture):
    by_url = {source.url: FIXTURE_BY_SOURCE[source.name] for source in ALL_SOURCES}
    return lambda url: load_fixture(by_url[url])


@pytest.fixture
def scanned(tmp_path, fixture_fetch) -> RegistryDocument:
    """A registry document exported from a separate store scanned against the fixtures."""
    source = Store(tmp_path / "source.db")
    outcome = run_scan(source, ALL_SOURCES, fixture_fetch, T1)
    return build_document(source, T1, outcome.sources_ok, outcome.failures)


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


def test_seen_all_returns_every_key_with_first_and_last_seen(store: Store) -> None:
    store.save_scan({rec("a").key: rec("a")}, (), T1)
    store.save_scan({rec("a").key: rec("a"), rec("b").key: rec("b")}, (), T2)

    assert store.seen_all() == {rec("a").key: (T1, T2), rec("b").key: (T2, T2)}


def test_save_scan_remembers_which_sources_succeeded_and_why_others_failed(store: Store) -> None:
    assert store.last_scan_sources() == {}

    store.save_scan({}, (), T1, sources_ok=("gemini",), failures={"azure": "network down"})

    assert store.last_scan_sources() == {"azure": "network down", "gemini": "ok"}


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
    assert "network down" in store.last_scan_sources()["azure"]
    assert store.last_scan_sources()["gemini"] == "ok"


def test_source_that_was_down_during_the_baseline_joins_without_a_flood(store: Store, fixture_fetch) -> None:
    def outage(url: str) -> str:
        if "azure" in url:
            raise OSError("network down")
        return fixture_fetch(url)

    run_scan(store, ALL_SOURCES, outage, T1)
    outcome = run_scan(store, ALL_SOURCES, fixture_fetch, T2)

    assert outcome.events == ()
    assert any(key[0] == "azure" for key in store.load_records())


def test_import_into_an_empty_store_is_a_baseline(store: Store, scanned: RegistryDocument) -> None:
    outcome = run_import(store, scanned, T2)

    assert outcome.baseline is True
    assert outcome.events == ()
    assert outcome.failures == {}
    assert outcome.sources_ok == ("registry",)
    assert outcome.total_records == len(scanned.records)
    assert store.load_records() == {r.key: r.lifecycle_record() for r in scanned.records}
    assert store.last_scan_sources() == {"registry": "ok"}


def test_import_reports_a_moved_retirement_date_once(
    store: Store, scanned: RegistryDocument, fixture_fetch
) -> None:
    run_scan(store, ALL_SOURCES, fixture_fetch, T1)
    key = ("openai", "gpt-5.4-cyber", "")
    moved = scanned.model_copy(
        update={
            "records": tuple(
                r.model_copy(update={"retire_on": date(2026, 11, 1)}) if r.key == key else r
                for r in scanned.records
            )
        }
    )

    outcome = run_import(store, moved, T2)

    assert [(e.type, e.key) for e in outcome.events] == [(EventType.RETIREMENT_DATE_CHANGED, key)]
    assert store.load_records()[key].retire_on == date(2026, 11, 1)


def test_import_keeps_records_the_document_does_not_mention(store: Store) -> None:
    store.save_scan({rec("old").key: rec("old")}, (), T1)
    only_new = RegistryDocument(generated_at=T2, sources={}, records=(stored("new"),))

    outcome = run_import(store, only_new, T2)

    assert set(store.load_records()) == {rec("old").key, rec("new").key}
    assert outcome.total_records == 2
    assert outcome.baseline is False


def test_import_into_an_empty_store_keeps_the_document_timestamps(store: Store) -> None:
    document = RegistryDocument(
        generated_at=T1, sources={}, records=(stored("m", first_seen=T0, last_seen=T1),)
    )

    run_import(store, document, T2)

    assert store.seen(rec("m").key) == (T0, T1)


@pytest.mark.parametrize(
    ("store_saw_it_at", "document_last_seen", "expected"),
    [
        (T3, T2, (T3, T3)),
        (T1, T2, (T1, T2)),
        (T2, BEFORE_T2_IN_PLUS_5, (T2, T2)),
    ],
    ids=["store-is-newer", "document-is-newer", "newer-as-text-but-older-in-time"],
)
def test_import_into_a_store_that_knows_the_key_keeps_first_seen_and_the_later_last_seen(
    store: Store, store_saw_it_at: datetime, document_last_seen: datetime, expected: tuple[datetime, datetime]
) -> None:
    store.save_scan({rec("m").key: rec("m")}, (), store_saw_it_at)
    document = RegistryDocument(
        generated_at=T2, sources={}, records=(stored("m", first_seen=T0, last_seen=document_last_seen),)
    )

    run_import(store, document, T4)

    assert store.seen(rec("m").key) == expected


def test_scan_after_import_advances_last_seen_only_for_what_it_found(
    store: Store, scanned: RegistryDocument, fixture_fetch
) -> None:
    aged = RegistryDocument(
        generated_at=T1,
        sources={},
        records=(
            *(r.model_copy(update={"first_seen": T0}) for r in scanned.records),
            stored("gone-from-every-page", first_seen=T0, last_seen=T1),
        ),
    )
    run_import(store, aged, T2)

    run_scan(store, ALL_SOURCES, fixture_fetch, T3)

    assert store.seen(("openai", "gpt-5.4-cyber", "")) == (T0, T3)
    assert store.seen(rec("gone-from-every-page").key) == (T0, T1)
    assert {first for first, _ in store.seen_all().values()} == {T0}
