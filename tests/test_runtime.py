from datetime import datetime, timedelta, timezone

import pytest

from sundial_llm.config import Config
from sundial_llm.guard import IdentityResolver
from sundial_llm.policy import Action
from sundial_llm.runtime import AutoScanner, build_guard
from sundial_llm.sources import ALL_SOURCES
from sundial_llm.store import Store

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)

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


def scanner(store: Store, fetch, hours: float = 24, clock=lambda: NOW) -> AutoScanner:
    return AutoScanner(
        store, Config(auto_scan_hours=hours), fetch, clock=clock, spawn=inline, post=lambda u, b: None
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
        store, Config(), fixture_fetch, clock=lambda: NOW, spawn=pending.append, post=lambda u, b: None
    )

    assert subject.maybe_start() is True
    assert subject.maybe_start() is False
    pending[0]()
    assert len(store.load_records()) > 100


def test_built_guard_scans_on_first_use_and_then_decides_from_real_data(tmp_path, fixture_fetch) -> None:
    config = Config(db=tmp_path / "guard.db")

    guard = build_guard(config, IdentityResolver(), fetch=fixture_fetch, clock=lambda: NOW, spawn=inline)
    decision = guard.decide("gpt-4-0613")

    assert decision.action is Action.WARN
    assert decision.days_left == 34
    assert guard.decide("gpt-6-astra").action is Action.ALLOW
