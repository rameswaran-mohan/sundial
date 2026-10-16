import json
from datetime import date, datetime, timezone

import pytest

from sundial_llm.models import LifecycleState
from sundial_llm.registry import (
    SCHEMA_VERSION,
    RegistryDocument,
    StoredRecord,
    build_document,
    parse_document,
    to_json,
    write_if_changed,
)
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


def stored(model_id: str = "m", platform: str = "openai", **extra: object) -> StoredRecord:
    base = {
        "platform": platform,
        "model_id": model_id,
        "state": LifecycleState.DEPRECATED,
        "source_url": "u",
        "evidence": "e",
        "retire_on": date(2026, 10, 1),
        "first_seen": T1,
        "last_seen": T1,
    }
    return StoredRecord(**{**base, **extra})


def document(*records: StoredRecord, generated_at: datetime = T1) -> RegistryDocument:
    return RegistryDocument(generated_at=generated_at, sources={"openai-deprecations": "ok"}, records=records)


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "sundial.db")


@pytest.fixture
def fixture_fetch(load_fixture):
    by_url = {source.url: FIXTURE_BY_SOURCE[source.name] for source in ALL_SOURCES}
    return lambda url: load_fixture(by_url[url])


def test_to_json_is_identical_whatever_order_the_records_were_added_in() -> None:
    a, b, c = stored("b"), stored("a"), stored("z", platform="azure")

    first, second = to_json(document(a, b, c)), to_json(document(c, a, b))

    assert first == second
    assert [r["model_id"] for r in json.loads(first)["records"]] == ["z", "a", "b"]
    assert first.startswith('{\n  "schema_version": 1,\n  "generated_at": ')
    assert first.endswith("}\n") and not first.endswith("\n\n")


def test_non_ascii_evidence_is_written_as_is_and_round_trips() -> None:
    original = document(stored(evidence="Ersetzt durch gpt-5 ab März, voir la dépréciation"))

    text = to_json(original)

    assert "März, voir la dépréciation" in text
    assert parse_document(text) == original


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (to_json(document(stored())).replace('"schema_version": 1', '"schema_version": 2'), "schema_version"),
        ("{}", "schema_version"),
        ("not json", "not JSON"),
    ],
    ids=["schema-version-2", "empty-object", "not-json"],
)
def test_parse_document_rejects_what_it_cannot_trust(text: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        parse_document(text)


def test_parse_document_rejects_an_invalid_record() -> None:
    data = json.loads(to_json(document(stored())))
    data["records"][0]["state"] = "sunsetting"

    with pytest.raises(ValueError):
        parse_document(json.dumps(data))


def test_write_if_changed_writes_a_missing_file(tmp_path) -> None:
    path = tmp_path / "registry" / "lifecycle.json"

    assert write_if_changed(document(stored()), path) is True
    assert path.read_bytes() == to_json(document(stored())).encode("utf-8")


def test_write_if_changed_ignores_a_new_generated_at(tmp_path) -> None:
    path = tmp_path / "lifecycle.json"
    write_if_changed(document(stored()), path)

    assert write_if_changed(document(stored(), generated_at=T2), path) is False
    assert parse_document(path.read_text(encoding="utf-8")).generated_at == T1


def test_write_if_changed_ignores_a_record_that_was_only_seen_again(tmp_path) -> None:
    path = tmp_path / "lifecycle.json"
    write_if_changed(document(stored()), path)

    assert write_if_changed(document(stored(last_seen=T2), generated_at=T2), path) is False


def test_write_if_changed_writes_when_a_retirement_date_moves(tmp_path) -> None:
    path = tmp_path / "lifecycle.json"
    write_if_changed(document(stored("a"), stored("b")), path)

    assert write_if_changed(document(stored("a"), stored("b", retire_on=date(2026, 11, 1))), path) is True
    assert parse_document(path.read_text(encoding="utf-8")).records[1].retire_on == date(2026, 11, 1)


def test_write_if_changed_overwrites_a_garbage_file(tmp_path) -> None:
    path = tmp_path / "lifecycle.json"
    path.write_text("<<< merge conflict", encoding="utf-8")

    assert write_if_changed(document(stored()), path) is True
    assert parse_document(path.read_text(encoding="utf-8")) == document(stored())


def test_build_document_carries_every_record_with_its_timestamps(store: Store, fixture_fetch) -> None:
    outcome = run_scan(store, ALL_SOURCES, fixture_fetch, T1)

    built = build_document(store, T2, outcome.sources_ok, outcome.failures)

    assert built.schema_version == SCHEMA_VERSION
    assert built.generated_at == T2
    assert built.sources == {name: "ok" for name in FIXTURE_BY_SOURCE}
    assert len(built.records) == len(store.load_records())
    assert all(r.first_seen == r.last_seen == T1 for r in built.records)
    assert [r.key for r in built.records] == sorted(r.key for r in built.records)


def test_build_document_reports_why_a_source_failed(store: Store, fixture_fetch) -> None:
    def outage(url: str) -> str:
        if "azure" in url:
            raise OSError("network down")
        return fixture_fetch(url)

    outcome = run_scan(store, ALL_SOURCES, outage, T1)

    built = build_document(store, T1, outcome.sources_ok, outcome.failures)

    assert "network down" in built.sources["azure"]
    assert built.sources["gemini"] == "ok"
