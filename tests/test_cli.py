import io
import json
import re
from datetime import datetime, timezone

import pytest

from sundial_llm.cli import main
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


@pytest.fixture
def fixture_fetch(load_fixture):
    by_url = {source.url: FIXTURE_BY_SOURCE[source.name] for source in ALL_SOURCES}
    return lambda url: load_fixture(by_url[url])


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cli.db"


def run(args: list[str], fetch, now=T1, env=None) -> tuple[int, str]:
    out = io.StringIO()
    code = main(args, fetch=fetch, now=lambda: now, post=lambda url, body: None, out=out, env=env or {})
    return code, out.getvalue()


def test_scan_creates_a_baseline_and_says_so(db, fixture_fetch) -> None:
    code, output = run(["scan", "--db", str(db)], fixture_fetch)

    assert code == 0
    assert "6 ok, 0 failed" in output
    assert "Baseline created" in output
    assert "cohere.command-r-v1:0" in output
    assert len(Store(db).load_records()) > 400


def test_scan_fails_loudly_when_every_source_is_down(db) -> None:
    def offline(url: str) -> str:
        raise OSError("no network")

    code, output = run(["scan", "--db", str(db)], offline)

    assert code == 1
    assert "0 ok, 6 failed" in output
    assert "no network" in output


def test_second_scan_prints_what_changed(db, fixture_fetch) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)

    def moved(url: str) -> str:
        return fixture_fetch(url).replace(
            "| Oct 1, 2026   | `gpt-5.4-cyber`", "| Nov 1, 2026   | `gpt-5.4-cyber`"
        )

    code, output = run(["scan", "--db", str(db)], moved, now=T2)

    assert code == 0
    assert "1 change" in output
    assert "gpt-5.4-cyber retirement moved from 2026-10-01 to 2026-11-01" in output


def test_status_lists_upcoming_retirements_soonest_first(db, fixture_fetch) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)

    code, output = run(["status", "--db", str(db), "--within", "40"], fixture_fetch)

    lines = [line for line in output.splitlines() if "gpt-5.4-cyber" in line or "gpt-4-0613" in line]
    assert code == 0
    assert "12d" in lines[0] and "gpt-5.4-cyber" in lines[0] and "gpt-5.6-cyber" in lines[0]
    assert "34d" in lines[1] and "gpt-4-0613" in lines[1]


def test_status_can_be_limited_to_one_platform(db, fixture_fetch) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)

    _, output = run(["status", "--db", str(db), "--within", "400", "--platform", "bedrock"], fixture_fetch)

    rows = [line for line in output.splitlines() if re.match(r"\s*\d+d\s", line)]
    assert rows and all(" bedrock " in row for row in rows)


def test_status_on_an_empty_registry_tells_you_to_scan(db, fixture_fetch) -> None:
    code, output = run(["status", "--db", str(db)], fixture_fetch)

    assert code == 0
    assert "sundial scan" in output


def test_check_flags_a_retiring_model_with_a_distinct_exit_code(db, fixture_fetch) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)

    code, output = run(["check", "gpt-4-0613", "--db", str(db)], fixture_fetch)

    assert code == 3
    assert "WARN" in output and "2026-10-23" in output and "34 days" in output and "gpt-5.6-sol" in output


def test_check_uses_base_model_for_azure_deployments(db, fixture_fetch) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)

    code, output = run(
        ["check", "azure/prod-chat", "--base-model", "azure/gpt-4o-2024-05-13", "--db", str(db)],
        fixture_fetch,
    )

    assert code == 3
    assert "2026-10-01" in output and "gpt-5.1" in output


def test_check_of_an_untracked_model_is_clean(db, fixture_fetch) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)

    code, output = run(["check", "gpt-never-heard-of-it", "--db", str(db)], fixture_fetch)

    assert code == 0
    assert "no lifecycle record" in output


def test_events_lists_history_newest_first(db, fixture_fetch) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    run(
        ["scan", "--db", str(db)],
        lambda url: fixture_fetch(url).replace(
            "| Oct 1, 2026   | `gpt-5.4-cyber`", "| Nov 1, 2026   | `gpt-5.4-cyber`"
        ),
        now=T2,
    )

    code, output = run(["events", "--db", str(db)], fixture_fetch)

    assert code == 0
    assert "2026-09-20" in output and "retirement_date_changed" in output and "gpt-5.4-cyber" in output


def test_costmap_patch_prints_changes_and_writes_a_patch_file(db, fixture_fetch, tmp_path) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    cost_map = tmp_path / "cost_map.json"
    cost_map.write_text(
        json.dumps({"gpt-4-0613": {"litellm_provider": "openai", "deprecation_date": "2025-06-06"}}),
        encoding="utf-8",
    )
    patch_file = tmp_path / "patch.json"

    code, output = run(
        ["costmap-patch", "--db", str(db), "--cost-map", str(cost_map), "--out", str(patch_file)],
        fixture_fetch,
    )

    assert code == 0
    assert "gpt-4-0613" in output and "2025-06-06" in output and "2026-10-23" in output
    assert json.loads(patch_file.read_text(encoding="utf-8")) == {
        "gpt-4-0613": {"deprecation_date": "2026-10-23"}
    }


def azure_down(fixture_fetch):
    def fetch(url: str) -> str:
        if "azure" in url:
            raise OSError("network down")
        return fixture_fetch(url)

    return fetch


def test_scan_strict_fails_when_any_source_fails(db, fixture_fetch) -> None:
    plain_code, plain = run(["scan", "--db", str(db)], azure_down(fixture_fetch))
    strict_code, strict = run(["scan", "--strict", "--db", str(db)], azure_down(fixture_fetch), now=T2)

    assert plain_code == 0
    assert strict_code == 1
    assert "5 ok, 1 failed" in plain and "5 ok, 1 failed" in strict
    assert "network down" in strict


def test_scan_strict_passes_when_every_source_is_up(db, fixture_fetch) -> None:
    code, _ = run(["scan", "--strict", "--db", str(db)], fixture_fetch)

    assert code == 0


def test_export_writes_every_record_then_reports_unchanged(db, fixture_fetch, tmp_path) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    target = tmp_path / "registry" / "lifecycle.json"

    code, output = run(["export", "--out", str(target), "--db", str(db)], fixture_fetch)
    document = json.loads(target.read_text(encoding="utf-8"))
    tracked = len(Store(db).load_records())

    assert code == 0
    assert f"Registry written to {target} ({tracked} records)" in output
    assert len(document["records"]) == tracked
    assert document["sources"] == {name: "ok" for name in FIXTURE_BY_SOURCE}

    run(["scan", "--db", str(db)], fixture_fetch, now=T2)
    code, output = run(["export", "--out", str(target), "--db", str(db)], fixture_fetch, now=T2)

    assert code == 0
    assert f"Registry unchanged ({tracked} records)" in output


def test_export_records_why_a_source_failed(db, fixture_fetch, tmp_path) -> None:
    run(["scan", "--db", str(db)], azure_down(fixture_fetch))
    target = tmp_path / "lifecycle.json"

    run(["export", "--out", str(target), "--db", str(db)], fixture_fetch)

    assert "network down" in json.loads(target.read_text(encoding="utf-8"))["sources"]["azure"]


def test_import_of_an_exported_file_creates_a_baseline(db, fixture_fetch, tmp_path) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    target = tmp_path / "lifecycle.json"
    run(["export", "--out", str(target), "--db", str(db)], fixture_fetch)
    fresh = tmp_path / "fresh.db"

    code, output = run(["import", str(target), "--db", str(fresh)], fixture_fetch, now=T2)

    assert code == 0
    assert "1 ok, 0 failed" in output
    assert "Baseline created" in output
    assert Store(fresh).load_records() == Store(db).load_records()


def test_import_fetches_a_url_with_the_injected_fetch(db, fixture_fetch, tmp_path) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    target = tmp_path / "lifecycle.json"
    run(["export", "--out", str(target), "--db", str(db)], fixture_fetch)
    url = "https://example.test/registry/lifecycle.json"
    requested: list[str] = []

    def fetch(asked: str) -> str:
        requested.append(asked)
        return target.read_text(encoding="utf-8")

    code, output = run(["import", url, "--db", str(tmp_path / "fresh.db")], fetch, now=T2)

    assert code == 0
    assert requested == [url]
    assert "Baseline created" in output


def test_import_of_something_that_is_not_a_registry_document_exits_2(db, fixture_fetch, tmp_path) -> None:
    bad = tmp_path / "lifecycle.json"
    bad.write_text("{}", encoding="utf-8")

    code, output = run(["import", str(bad), "--db", str(db)], fixture_fetch)

    assert code == 2
    assert "schema_version" in output
    assert Store(db).load_records() == {}


def test_import_of_an_unreadable_source_exits_1(db, fixture_fetch, tmp_path) -> None:
    code, output = run(["import", str(tmp_path / "missing.json"), "--db", str(db)], fixture_fetch)

    assert code == 1
    assert "missing.json" in output


def test_config_file_supplies_the_database_path(tmp_path, fixture_fetch) -> None:
    config = tmp_path / "sundial.yaml"
    config.write_text(f"db: {(tmp_path / 'from-config.db').as_posix()}\n", encoding="utf-8")

    code, _ = run(["scan", "--config", str(config)], fixture_fetch)

    assert code == 0
    assert (tmp_path / "from-config.db").exists()
