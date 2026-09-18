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


def test_config_file_supplies_the_database_path(tmp_path, fixture_fetch) -> None:
    config = tmp_path / "sundial.yaml"
    config.write_text(f"db: {(tmp_path / 'from-config.db').as_posix()}\n", encoding="utf-8")

    code, _ = run(["scan", "--config", str(config)], fixture_fetch)

    assert code == 0
    assert (tmp_path / "from-config.db").exists()
