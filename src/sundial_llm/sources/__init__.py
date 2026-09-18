"""Collectors for official provider lifecycle pages."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import httpx

from sundial_llm import __version__
from sundial_llm.models import LifecycleRecord, ParseResult, RecordKey, merge_records
from sundial_llm.sources import anthropic, azure, bedrock, gemini, openai_changelog, openai_deprecations

USER_AGENT = f"sundial-llm/{__version__} (+https://github.com/rameswaran-mohan/sundial)"

Fetch = Callable[[str], str]


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    parse: Callable[[str], ParseResult]


ALL_SOURCES: tuple[Source, ...] = (
    Source("openai-deprecations", openai_deprecations.SOURCE_URL, openai_deprecations.parse),
    Source("openai-changelog", openai_changelog.SOURCE_URL, openai_changelog.parse),
    Source("anthropic", anthropic.SOURCE_URL, anthropic.parse),
    Source("azure", azure.SOURCE_URL, azure.parse),
    Source("gemini", gemini.SOURCE_URL, gemini.parse),
    Source("bedrock", bedrock.SOURCE_URL, bedrock.parse),
)


@dataclass(frozen=True)
class CollectResult:
    records: dict[RecordKey, LifecycleRecord]
    warnings: tuple[str, ...] = ()
    failures: dict[str, str] = field(default_factory=dict)
    keys_by_source: dict[str, frozenset[RecordKey]] = field(default_factory=dict)


def collect(sources: Iterable[Source], fetch: Fetch) -> CollectResult:
    """Fetch and parse every source. One source failing never stops the others."""
    records: dict[RecordKey, LifecycleRecord] = {}
    warnings: list[str] = []
    failures: dict[str, str] = {}
    keys_by_source: dict[str, frozenset[RecordKey]] = {}
    for source in sources:
        try:
            parsed = source.parse(fetch(source.url))
        except Exception as error:  # noqa: BLE001 - any source error is reported, never raised
            failures[source.name] = f"{type(error).__name__}: {error}"
            continue
        warnings.extend(parsed.warnings)
        keys_by_source[source.name] = frozenset(record.key for record in parsed.records)
        for record in parsed.records:
            existing = records.get(record.key)
            records[record.key] = record if existing is None else merge_records(existing, record)
    return CollectResult(records, tuple(warnings), failures, keys_by_source)


def http_fetch(
    url: str,
    *,
    client: httpx.Client | None = None,
    attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    owned = client is None
    active = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        for attempt in range(1, attempts + 1):
            try:
                response = active.get(url, headers={"User-Agent": USER_AGENT})
                response.raise_for_status()
                return response.text
            except (httpx.TransportError, httpx.HTTPStatusError):
                if attempt == attempts:
                    raise
                sleep(2.0 * attempt)
        raise RuntimeError("unreachable")
    finally:
        if owned:
            active.close()
