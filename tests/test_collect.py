from datetime import date

import httpx
import pytest

from sundial_llm.models import LifecycleRecord, LifecycleState, ParseResult
from sundial_llm.sources import ALL_SOURCES, Source, collect, http_fetch

FIXTURE_BY_SOURCE = {
    "openai-deprecations": "openai_deprecations.md",
    "openai-changelog": "openai_changelog.md",
    "anthropic": "anthropic_deprecations.md",
    "azure": "azure_retirement_schedule.md",
    "gemini": "gemini_deprecations.md",
    "bedrock": "bedrock_lifecycle_legacy.md",
}


@pytest.fixture
def fixture_fetch(load_fixture):
    by_url = {source.url: FIXTURE_BY_SOURCE[source.name] for source in ALL_SOURCES}
    return lambda url: load_fixture(by_url[url])


def rec(state: LifecycleState, **extra: object) -> LifecycleRecord:
    return LifecycleRecord(
        platform="openai", model_id="gpt-x", state=state, source_url="u", evidence="e", **extra
    )


def test_every_release_one_source_is_registered() -> None:
    assert {source.name for source in ALL_SOURCES} == set(FIXTURE_BY_SOURCE)


def test_collect_gathers_records_for_all_five_platforms(fixture_fetch) -> None:
    result = collect(ALL_SOURCES, fixture_fetch)

    assert {key[0] for key in result.records} == {"openai", "anthropic", "azure", "gemini", "bedrock"}
    assert result.failures == {}


def test_one_failing_source_does_not_stop_the_others(fixture_fetch) -> None:
    def flaky(url: str) -> str:
        if "anthropic" in url or "claude" in url:
            raise httpx.ConnectError("boom")
        return fixture_fetch(url)

    result = collect(ALL_SOURCES, flaky)

    assert "anthropic" in result.failures
    assert "boom" in result.failures["anthropic"]
    assert any(key[0] == "azure" for key in result.records)
    assert not any(key[0] == "anthropic" for key in result.records)


def test_page_that_parses_to_nothing_is_reported_as_a_failure(fixture_fetch) -> None:
    def emptied(url: str) -> str:
        return "# nothing" if "gemini" in url or "google" in url else fixture_fetch(url)

    result = collect(ALL_SOURCES, emptied)

    assert "page shape changed" in result.failures["gemini"]


def test_same_key_from_two_sources_is_merged() -> None:
    launches = Source(
        "a",
        "https://a.test",
        lambda _: ParseResult((rec(LifecycleState.ACTIVE, released_on=date(2025, 1, 1)),)),
    )
    retirements = Source(
        "b",
        "https://b.test",
        lambda _: ParseResult((rec(LifecycleState.DEPRECATED, retire_on=date(2026, 1, 1)),)),
    )

    result = collect((launches, retirements), lambda url: "")

    merged = result.records[("openai", "gpt-x", "")]
    assert merged.state is LifecycleState.DEPRECATED
    assert merged.released_on == date(2025, 1, 1)
    assert merged.retire_on == date(2026, 1, 1)


def test_parser_warnings_are_passed_through(fixture_fetch) -> None:
    result = collect(ALL_SOURCES, fixture_fetch)

    assert any("cohere.command-r-v1:0" in warning for warning in result.warnings)


def test_http_fetch_retries_server_errors_and_identifies_itself() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503) if len(seen) < 3 else httpx.Response(200, text="# ok")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        body = http_fetch("https://docs.test/page.md", client=client, sleep=lambda _: None)

    assert body == "# ok"
    assert len(seen) == 3
    assert "sundial-llm" in seen[0].headers["user-agent"]


def test_http_fetch_gives_up_after_the_last_attempt() -> None:
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        http_fetch("https://docs.test/page.md", client=client, sleep=lambda _: None)
