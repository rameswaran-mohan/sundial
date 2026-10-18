import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from sundial_llm import runtime as runtime_module
from sundial_llm.config import Config, NotifyConfig
from sundial_llm.guard import IdentityResolver
from sundial_llm.models import LifecycleState
from sundial_llm.policy import Action
from sundial_llm.registry import RegistryDocument, StoredRecord, build_document, parse_document, to_json
from sundial_llm.runtime import AutoScanner, build_guard, build_runtime
from sundial_llm.scan import run_import, run_scan
from sundial_llm.sources import ALL_SOURCES, Fetch
from sundial_llm.store import Store
from sundial_llm.usage import UsageRow

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
REGISTRY_URL = "https://registry.test/lifecycle.json"
PROVIDER_URLS = {source.url for source in ALL_SOURCES}
CYBER = ("openai", "gpt-5.4-cyber", "")

FIXTURE_BY_SOURCE = {
    "openai-deprecations": "openai_deprecations.md",
    "openai-changelog": "openai_changelog.md",
    "anthropic": "anthropic_deprecations.md",
    "azure": "azure_retirement_schedule.md",
    "gemini": "gemini_deprecations.md",
    "bedrock": "bedrock_lifecycle_legacy.md",
}


def inline(job) -> None:
    job()


@pytest.fixture
def fixture_fetch(load_fixture):
    by_url = {source.url: FIXTURE_BY_SOURCE[source.name] for source in ALL_SOURCES}
    return lambda url: load_fixture(by_url[url])


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "sundial.db")


def scanner(
    store: Store, fetch, hours: float = 24, clock=lambda: NOW, registry_url: str | None = None
) -> AutoScanner:
    config = Config(auto_scan_hours=hours, registry_url=registry_url)
    return AutoScanner(store, config, fetch, clock=clock, spawn=inline, post=lambda u, b: None)


def serving(fetched: list[str], registry: str | Exception, providers: Fetch) -> Fetch:
    """Serve `registry` at REGISTRY_URL (raising it if it is an exception) and `providers` elsewhere."""

    def fetch(url: str) -> str:
        fetched.append(url)
        if url != REGISTRY_URL:
            return providers(url)
        if isinstance(registry, Exception):
            raise registry
        return registry

    return fetch


def offline(url: str) -> str:
    raise OSError(f"offline: {url}")


def published(tmp_path: Path, fetch: Fetch, generated_at: datetime) -> str:
    """The registry document a nightly run would publish after scanning the provider pages."""
    nightly = Store(tmp_path / "nightly.db")
    outcome = run_scan(nightly, ALL_SOURCES, fetch, generated_at)
    return to_json(build_document(nightly, generated_at, outcome.sources_ok, outcome.failures))


def document(generated_at: datetime, *records: StoredRecord) -> str:
    return to_json(RegistryDocument(generated_at=generated_at, sources={"anthropic": "ok"}, records=records))


ONLY_IN_THE_REGISTRY = StoredRecord(
    platform="openai",
    model_id="gpt-only-in-the-registry",
    state=LifecycleState.ACTIVE,
    source_url="https://registry.test/page",
    evidence="e",
    first_seen=NOW - timedelta(days=1),
    last_seen=NOW - timedelta(days=1),
)


def test_store_that_was_never_scanned_is_scanned(store: Store, fixture_fetch) -> None:
    assert scanner(store, fixture_fetch).maybe_start() is True
    assert len(store.load_records()) > 100
    assert store.last_scan_at() == NOW


def test_fresh_store_is_left_alone(store: Store, fixture_fetch) -> None:
    scanner(store, fixture_fetch).maybe_start()
    fetched: list[str] = []

    def counting(url: str) -> str:
        fetched.append(url)
        return fixture_fetch(url)

    later = scanner(store, counting, clock=lambda: NOW + timedelta(hours=23))

    assert later.maybe_start() is False
    assert fetched == []


def test_stale_store_is_scanned_again(store: Store, fixture_fetch) -> None:
    scanner(store, fixture_fetch).maybe_start()

    assert scanner(store, fixture_fetch, clock=lambda: NOW + timedelta(hours=25)).maybe_start() is True


def test_zero_hours_disables_auto_scan(store: Store, fixture_fetch) -> None:
    assert scanner(store, fixture_fetch, hours=0).maybe_start() is False
    assert store.load_records() == {}


def test_scan_that_blows_up_is_swallowed(store: Store) -> None:
    def broken(url: str) -> str:
        raise OSError("offline")

    assert scanner(store, broken).maybe_start() is True
    assert store.load_records() == {}


def test_only_one_scan_runs_at_a_time(store: Store, fixture_fetch) -> None:
    pending: list = []
    subject = AutoScanner(
        store,
        Config(registry_url=None),
        fixture_fetch,
        clock=lambda: NOW,
        spawn=pending.append,
        post=lambda u, b: None,
    )

    assert subject.maybe_start() is True
    assert subject.maybe_start() is False
    pending[0]()
    assert len(store.load_records()) > 100


def test_built_guard_scans_on_first_use_and_then_decides_from_real_data(tmp_path, fixture_fetch) -> None:
    config = Config(db=tmp_path / "guard.db", registry_url=None)

    guard = build_guard(config, IdentityResolver(), fetch=fixture_fetch, clock=lambda: NOW, spawn=inline)
    decision = guard.decide("gpt-4-0613")

    assert decision.action is Action.WARN
    assert decision.days_left == 34
    assert guard.decide("gpt-6-astra").action is Action.ALLOW


def test_runtime_recorder_writes_usage_to_the_configured_database_and_prunes_it(
    tmp_path, fixture_fetch
) -> None:
    config = Config(db=tmp_path / "runtime.db", registry_url=None, usage_retention_days=7)
    Store(config.db).add_usage([UsageRow(NOW.date() - timedelta(days=8), "gpt-old", "", "", "", "", 1)])

    runtime = build_runtime(
        config,
        IdentityResolver(),
        fetch=fixture_fetch,
        clock=lambda: NOW,
        spawn=inline,
        at_exit=lambda f: None,
    )
    decision = runtime.guard.decide("gpt-4-0613")
    assert runtime.recorder is not None
    runtime.recorder.record("gpt-4-0613", None, decision.key)
    runtime.recorder.close()

    assert Store(config.db).usage_rows(date.min) == [
        UsageRow(NOW.date(), "gpt-4-0613", "", "openai", "gpt-4-0613", "", 1)
    ]


def test_runtime_hands_due_usage_flushes_to_its_spawn(tmp_path, fixture_fetch) -> None:
    jobs: list = []
    config = Config(db=tmp_path / "runtime.db", registry_url=None, auto_scan_hours=0)

    runtime = build_runtime(
        config,
        IdentityResolver(),
        fetch=fixture_fetch,
        clock=lambda: NOW,
        spawn=jobs.append,
        at_exit=lambda f: None,
    )
    assert runtime.recorder is not None
    for _ in range(50):  # the default number of calls between flushes
        runtime.recorder.record("gpt-mystery", None, None)

    assert len(jobs) == 1
    assert Store(config.db).usage_rows(date.min) == []
    jobs[0]()
    assert Store(config.db).usage_rows(date.min) == [UsageRow(NOW.date(), "gpt-mystery", "", "", "", "", 50)]


def test_runtime_registers_its_recorder_to_flush_at_exit(tmp_path, fixture_fetch) -> None:
    registered: list = []

    runtime = build_runtime(
        Config(db=tmp_path / "runtime.db", registry_url=None),
        IdentityResolver(),
        fetch=fixture_fetch,
        clock=lambda: NOW,
        spawn=inline,
        at_exit=registered.append,
    )

    assert runtime.recorder is not None
    assert registered == [runtime.recorder.close]


def test_a_bare_guard_creates_no_recorder_to_flush_at_exit(
    tmp_path, fixture_fetch, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_recorder(*args, **kwargs):
        raise AssertionError("build_guard returns only the guard, so it must not create a recorder")

    monkeypatch.setattr(runtime_module, "UsageRecorder", no_recorder)

    guard = build_guard(
        Config(db=tmp_path / "guard.db", registry_url=None),
        IdentityResolver(),
        fetch=fixture_fetch,
        clock=lambda: NOW,
        spawn=inline,
    )

    assert guard.decide("gpt-4-0613").action is Action.WARN


def test_usage_tracking_off_gives_a_runtime_without_a_recorder(tmp_path, fixture_fetch) -> None:
    config = Config(db=tmp_path / "runtime.db", registry_url=None, usage_tracking=False)
    registered: list = []

    runtime = build_runtime(
        config,
        IdentityResolver(),
        fetch=fixture_fetch,
        clock=lambda: NOW,
        spawn=inline,
        at_exit=registered.append,
    )

    assert runtime.recorder is None
    assert registered == []
    assert runtime.guard.decide("gpt-4-0613").action is Action.WARN


def test_published_registry_fills_the_store_without_fetching_a_provider(
    store: Store, tmp_path: Path, fixture_fetch
) -> None:
    text = published(tmp_path, fixture_fetch, NOW - timedelta(hours=9))
    fetched: list[str] = []
    subject = scanner(store, serving(fetched, text, offline), registry_url=REGISTRY_URL)

    assert subject.maybe_start() is True

    assert subject.last_mode == "registry"
    assert fetched == [REGISTRY_URL]
    expected = {stored.key: stored.lifecycle_record() for stored in parse_document(text).records}
    assert len(expected) > 100
    assert store.load_records() == expected
    assert store.last_scan_at() == NOW


def test_unreachable_registry_falls_back_to_scanning_the_providers(
    store: Store, fixture_fetch, caplog: pytest.LogCaptureFixture
) -> None:
    fetched: list[str] = []
    subject = scanner(store, serving(fetched, OSError("offline"), fixture_fetch), registry_url=REGISTRY_URL)

    assert subject.maybe_start() is True

    assert subject.last_mode == "scan"
    assert len(store.load_records()) > 100
    assert set(fetched) == {REGISTRY_URL} | PROVIDER_URLS
    assert (
        f"sundial: registry {REGISTRY_URL} unavailable (offline); scanning sources directly"
        in caplog.messages
    )


def test_registry_that_is_not_json_falls_back_to_scanning(store: Store, fixture_fetch) -> None:
    fetched: list[str] = []
    subject = scanner(store, serving(fetched, "not json", fixture_fetch), registry_url=REGISTRY_URL)

    assert subject.maybe_start() is True

    assert subject.last_mode == "scan"
    assert len(store.load_records()) > 100


def test_disabled_registry_is_never_requested(store: Store, fixture_fetch) -> None:
    fetched: list[str] = []
    subject = scanner(
        store, serving(fetched, OSError("must not be fetched"), fixture_fetch), registry_url=None
    )

    assert subject.maybe_start() is True

    assert subject.last_mode == "scan"
    assert set(fetched) == PROVIDER_URLS


def test_unchanged_published_registry_is_imported_every_day_without_scanning(
    store: Store, tmp_path: Path, fixture_fetch
) -> None:
    text = published(tmp_path, fixture_fetch, NOW - timedelta(hours=9))  # never rewritten afterwards
    fetched: list[str] = []
    modes: list[str | None] = []

    for day in range(3):
        today = NOW + timedelta(hours=25 * day)
        subject = scanner(
            store, serving(fetched, text, offline), clock=lambda today=today: today, registry_url=REGISTRY_URL
        )
        assert subject.maybe_start() is True
        modes.append(subject.last_mode)

    assert modes == ["registry", "registry", "registry"]
    assert set(fetched) == {REGISTRY_URL}
    assert store.last_direct_scan_at() is None


def scan_while_the_registry_is_down(store: Store, fetch: Fetch) -> None:
    """Leave the store with a direct scan at NOW, made because the registry could not be fetched."""
    subject = scanner(store, serving([], OSError("offline"), fetch), registry_url=REGISTRY_URL)
    assert subject.maybe_start() is True
    assert subject.last_mode == "scan"


@pytest.mark.parametrize("age", [timedelta(hours=1), timedelta(0)], ids=["older", "same-time"])
def test_older_registry_that_would_roll_back_a_direct_scan_is_skipped_for_a_scan(
    store: Store, fixture_fetch, age: timedelta, caplog: pytest.LogCaptureFixture
) -> None:
    scan_while_the_registry_is_down(store, fixture_fetch)
    scanned_retire_on = store.load_records()[CYBER].retire_on
    assert scanned_retire_on != date(2026, 11, 1)
    local = build_document(store, NOW - age, ["openai-deprecations"], {})
    moved = local.model_copy(
        update={
            "records": tuple(
                r.model_copy(update={"retire_on": date(2026, 11, 1)}) if r.key == CYBER else r
                for r in local.records
            )
        }
    )
    fetched: list[str] = []
    later = NOW + timedelta(hours=25)
    subject = scanner(
        store, serving(fetched, to_json(moved), fixture_fetch), clock=lambda: later, registry_url=REGISTRY_URL
    )

    assert subject.maybe_start() is True

    assert subject.last_mode == "scan"
    assert store.load_records()[CYBER].retire_on == scanned_retire_on
    assert set(fetched) == {REGISTRY_URL} | PROVIDER_URLS
    assert store.last_direct_scan_at() == later
    assert any("would roll back" in message for message in caplog.messages)


def with_cyber(document: RegistryDocument, **change: object) -> str:
    """The document as JSON, with `change` applied to the gpt-5.4-cyber record."""
    records = tuple(r.model_copy(update=change) if r.key == CYBER else r for r in document.records)
    return to_json(document.model_copy(update={"records": records}))


@pytest.mark.parametrize("field", ["ambiguous", "retire_on_is_floor", "replacements", "deprecated_on"])
def test_older_registry_that_changes_a_field_without_an_event_is_still_skipped(
    store: Store, fixture_fetch, field: str
) -> None:
    scan_while_the_registry_is_down(store, fixture_fetch)
    scanned = store.load_records()[CYBER]
    change = {
        "ambiguous": not scanned.ambiguous,
        "retire_on_is_floor": not scanned.retire_on_is_floor,
        "replacements": (*scanned.replacements, "gpt-extra"),
        "deprecated_on": date(2020, 1, 1),
    }[field]
    assert getattr(scanned, field) != change
    text = with_cyber(
        build_document(store, NOW - timedelta(hours=1), ["openai-deprecations"], {}), **{field: change}
    )
    subject = scanner(
        store,
        serving([], text, fixture_fetch),
        clock=lambda: NOW + timedelta(hours=25),
        registry_url=REGISTRY_URL,
    )

    assert subject.maybe_start() is True

    assert subject.last_mode == "scan"
    assert store.load_records()[CYBER] == scanned


def forget_the_direct_scan_time(store: Store) -> None:
    """Make the store look like release 1, which never recorded when it last scanned directly."""
    with closing(sqlite3.connect(store.path)) as conn, conn:
        conn.execute("DELETE FROM meta WHERE key = 'last_direct_scan_at'")
    assert store.last_direct_scan_at() is None


def test_a_release_1_store_counts_its_last_scan_as_direct(store: Store, fixture_fetch) -> None:
    run_scan(store, ALL_SOURCES, fixture_fetch, NOW)
    forget_the_direct_scan_time(store)
    scanned_retire_on = store.load_records()[CYBER].retire_on
    text = with_cyber(
        build_document(store, NOW - timedelta(hours=1), ["openai-deprecations"], {}),
        retire_on=date(2026, 11, 1),
    )
    subject = scanner(
        store,
        serving([], text, fixture_fetch),
        clock=lambda: NOW + timedelta(hours=25),
        registry_url=REGISTRY_URL,
    )

    assert subject.maybe_start() is True

    assert subject.last_mode == "scan"
    assert store.load_records()[CYBER].retire_on == scanned_retire_on


def test_a_store_last_filled_from_the_registry_has_no_direct_scan_to_protect(
    store: Store, tmp_path: Path, fixture_fetch
) -> None:
    imported = published(tmp_path, fixture_fetch, NOW - timedelta(hours=2))
    run_import(store, parse_document(imported), NOW)
    assert store.last_direct_scan_at() is None
    text = with_cyber(
        parse_document(imported).model_copy(update={"generated_at": NOW - timedelta(hours=1)}),
        retire_on=date(2026, 11, 1),
    )
    fetched: list[str] = []
    subject = scanner(
        store,
        serving(fetched, text, offline),
        clock=lambda: NOW + timedelta(hours=25),
        registry_url=REGISTRY_URL,
    )

    assert subject.maybe_start() is True

    assert subject.last_mode == "registry"
    assert fetched == [REGISTRY_URL]
    assert store.load_records()[CYBER].retire_on == date(2026, 11, 1)


def test_older_registry_that_changes_nothing_is_imported(store: Store, fixture_fetch) -> None:
    scan_while_the_registry_is_down(store, fixture_fetch)
    text = to_json(build_document(store, NOW - timedelta(hours=1), ["openai-deprecations"], {}))
    fetched: list[str] = []
    subject = scanner(
        store,
        serving(fetched, text, offline),
        clock=lambda: NOW + timedelta(hours=25),
        registry_url=REGISTRY_URL,
    )

    assert subject.maybe_start() is True

    assert subject.last_mode == "registry"
    assert fetched == [REGISTRY_URL]
    assert store.last_direct_scan_at() == NOW


def test_newer_registry_is_imported_over_scanned_data_and_its_events_are_notified(
    store: Store, fixture_fetch
) -> None:
    scanner(store, fixture_fetch).maybe_start()  # local data from a direct scan at NOW
    key, record = next(
        (key, record)
        for key, record in sorted(store.load_records().items())
        if record.state is not LifecycleState.RETIRED
    )
    retired = StoredRecord(
        **{**record.model_dump(), "state": LifecycleState.RETIRED},
        first_seen=NOW,
        last_seen=NOW + timedelta(hours=2),
    )
    text = document(NOW + timedelta(hours=2), retired, ONLY_IN_THE_REGISTRY)
    fetched: list[str] = []
    posted: list[tuple[str, dict]] = []
    config = Config(registry_url=REGISTRY_URL, notify=NotifyConfig(webhook="https://hooks.test/generic"))
    subject = AutoScanner(
        store,
        config,
        serving(fetched, text, offline),
        clock=lambda: NOW + timedelta(hours=25),
        spawn=inline,
        post=lambda url, body: posted.append((url, body)),
    )

    assert subject.maybe_start() is True

    assert subject.last_mode == "registry"
    assert fetched == [REGISTRY_URL]
    records = store.load_records()
    assert records[key].state is LifecycleState.RETIRED
    assert ONLY_IN_THE_REGISTRY.key in records
    assert len(posted) == 1
    assert posted[0][0] == "https://hooks.test/generic"
    assert record.model_id in posted[0][1]["text"]
