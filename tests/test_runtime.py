from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sundial_llm.config import Config, NotifyConfig
from sundial_llm.guard import IdentityResolver
from sundial_llm.models import LifecycleState
from sundial_llm.policy import Action
from sundial_llm.registry import RegistryDocument, StoredRecord, build_document, parse_document, to_json
from sundial_llm.runtime import AutoScanner, build_guard
from sundial_llm.scan import run_scan
from sundial_llm.sources import ALL_SOURCES, Fetch
from sundial_llm.store import Store

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
REGISTRY_URL = "https://registry.test/lifecycle.json"
PROVIDER_URLS = {source.url for source in ALL_SOURCES}

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


@pytest.mark.parametrize("age", [timedelta(hours=1), timedelta(0)], ids=["older", "same-time"])
def test_registry_no_newer_than_the_local_data_is_skipped_for_a_scan(
    store: Store, fixture_fetch, age: timedelta, caplog: pytest.LogCaptureFixture
) -> None:
    scanner(store, fixture_fetch).maybe_start()  # local data from a direct scan at NOW
    text = document(NOW - age, ONLY_IN_THE_REGISTRY)
    fetched: list[str] = []
    later = NOW + timedelta(hours=25)
    subject = scanner(
        store, serving(fetched, text, fixture_fetch), clock=lambda: later, registry_url=REGISTRY_URL
    )

    assert subject.maybe_start() is True

    assert subject.last_mode == "scan"
    assert ONLY_IN_THE_REGISTRY.key not in store.load_records()
    assert set(fetched) == {REGISTRY_URL} | PROVIDER_URLS
    assert store.last_scan_at() == later
    assert any("is not newer than the local data" in message for message in caplog.messages)


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
