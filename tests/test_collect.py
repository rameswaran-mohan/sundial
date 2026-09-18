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


def test_fallback_url_is_used_when_the_primary_fails() -> None:
    source = Source(
        "s",
        "https://primary.test",
        lambda text: ParseResult((rec(LifecycleState.ACTIVE),)),
        "https://backup.test",
    )
    asked: list[str] = []

    def fetch(url: str) -> str:
        asked.append(url)
        if "primary" in url:
            raise httpx.ConnectError("primary down")
        return "ok"

    result = collect((source,), fetch)

    assert asked == ["https://primary.test", "https://backup.test"]
    assert result.failures == {}
    assert ("openai", "gpt-x", "") in result.records
    assert any("fallback" in warning and "primary down" in warning for warning in result.warnings)


def test_failure_names_both_urls_when_the_fallback_fails_too() -> None:
    source = Source(
        "s",
        "https://primary.test",
        lambda text: ParseResult((rec(LifecycleState.ACTIVE),)),
        "https://backup.test",
    )

    def fetch(url: str) -> str:
        raise httpx.ConnectError(f"down: {url}")

    result = collect((source,), fetch)

    assert "primary.test" in result.failures["s"] and "backup.test" in result.failures["s"]


def test_azure_reads_the_published_page_first_and_github_second() -> None:
    azure_source = next(source for source in ALL_SOURCES if source.name == "azure")

    assert azure_source.url.startswith("https://learn.microsoft.com/")
    assert azure_source.fallback_url is not None and "raw.githubusercontent.com" in azure_source.fallback_url


def test_http_fetch_asks_for_markdown() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="# ok")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        http_fetch("https://docs.test/page", client=client)

    assert seen[0].headers["accept"].startswith("text/markdown")
