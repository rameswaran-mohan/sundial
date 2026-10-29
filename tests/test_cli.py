import io
import json
import re
import sys
import threading
import types
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sundial_llm import cli
from sundial_llm.cli import main
from sundial_llm.integrations.service import DecisionService
from sundial_llm.runtime import build_runtime
from sundial_llm.sources import ALL_SOURCES
from sundial_llm.store import Store
from sundial_llm.usage import UsageRecorder, UsageRow

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


def run(args: list[str], fetch, now=T1, env=None, **options) -> tuple[int, str]:
    out = io.StringIO()
    code = main(
        args, fetch=fetch, now=lambda: now, post=lambda url, body: None, out=out, env=env or {}, **options
    )
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


TABLE_ROW = re.compile(r"\s*-?\d+d\s")


def seed_usage(db, day) -> None:
    Store(db).add_usage(
        [
            UsageRow(day, "gpt-4-0613", "payments", "openai", "gpt-4-0613", "", 4),
            UsageRow(day - timedelta(days=1), "gpt-4-0613", "payments", "openai", "gpt-4-0613", "", 3),
            UsageRow(day, "gpt-mystery", "", "", "", "", 2),
        ]
    )


def test_status_in_use_lists_only_retiring_models_your_traffic_uses(db, fixture_fetch) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    seed_usage(db, T1.date())
    tracked = len(Store(db).load_records())

    code, output = run(["status", "--in-use", "--days", "30", "--db", str(db)], fixture_fetch)

    lines = output.splitlines()
    rows = [line for line in lines if TABLE_ROW.match(line)]
    assert code == 0
    assert lines[0] == f"1 models in use retire within 90 days (2 models in use, {tracked} tracked)."
    assert rows == ["   34d  2026-10-23  openai     gpt-4-0613  calls=7  teams=payments"]
    assert lines[-2:] == ["Models in use with no lifecycle record:", "  gpt-mystery  calls=2"]


def test_status_in_use_lists_other_with_the_models_that_have_no_lifecycle_record(db, fixture_fetch) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    recorder = UsageRecorder(Store(db), clock=lambda: T1, max_untracked_names=1)
    for name in ("gpt-mystery", "junk-1", "junk-2", "gpt-mystery", "junk-3"):
        recorder.record(name, None, None)
    recorder.close()

    _, output = run(["status", "--in-use", "--db", str(db)], fixture_fetch)

    assert output.splitlines()[-3:] == [
        "Models in use with no lifecycle record:",
        "  (other)  calls=3",
        "  gpt-mystery  calls=2",
    ]


def test_status_in_use_lists_overdue_models_first_with_negative_days(db, fixture_fetch) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    seed_usage(db, T1.date())
    Store(db).add_usage(
        [UsageRow(T1.date(), "chatgpt-4o-latest", "support", "openai", "chatgpt-4o-latest", "", 5)]
    )

    _, output = run(["status", "--in-use", "--db", str(db)], fixture_fetch)
    _, plain = run(["status", "--db", str(db)], fixture_fetch)

    lines = output.splitlines()
    assert lines[0].startswith("2 models in use retire within 90 days (3 models in use, ")
    assert [line for line in lines if TABLE_ROW.match(line)] == [
        " -214d  2026-02-17  openai     chatgpt-4o-latest  calls=5  teams=support",
        "   34d  2026-10-23  openai     gpt-4-0613  calls=7  teams=payments",
    ]
    assert "chatgpt-4o-latest" not in plain


def test_status_in_use_names_the_upstream_model_when_the_public_name_differs(db, fixture_fetch) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    Store(db).add_usage(
        [
            UsageRow(T1.date(), "legacy-gpt4", "", "openai", "gpt-4-0613", "", 3),
            UsageRow(T1.date(), "prod-chat", "", "azure", "gpt-4o", "2024-05-13", 6),
        ]
    )

    _, output = run(["status", "--in-use", "--db", str(db)], fixture_fetch)

    assert [line for line in output.splitlines() if TABLE_ROW.match(line)] == [
        "   12d  2026-10-01  azure      prod-chat [gpt-4o (2024-05-13)]  calls=6  teams=-",
        "   34d  2026-10-23  openai     legacy-gpt4 [gpt-4-0613]  calls=3  teams=-",
    ]


def test_status_in_use_only_counts_usage_inside_the_look_back_window(db, fixture_fetch) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    seed_usage(db, T1.date() - timedelta(days=10))

    code, output = run(["status", "--in-use", "--days", "1", "--db", str(db)], fixture_fetch)

    assert code == 0
    assert output.startswith("0 models in use retire within 90 days (0 models in use, ")
    assert "gpt-4-0613" not in output and "gpt-mystery" not in output


def test_status_in_use_leaves_out_models_retiring_after_the_horizon(db, fixture_fetch) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    seed_usage(db, T1.date())

    _, output = run(["status", "--in-use", "--within", "20", "--db", str(db)], fixture_fetch)

    assert output.startswith("0 models in use retire within 20 days (2 models in use, ")
    assert not [line for line in output.splitlines() if TABLE_ROW.match(line)]
    assert "  gpt-mystery  calls=2" in output


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


def write_cost_map(tmp_path, entries: dict) -> str:
    cost_map = tmp_path / "cost_map.json"
    cost_map.write_text(json.dumps(entries), encoding="utf-8")
    return str(cost_map)


def test_costmap_patch_writes_a_drift_report_naming_the_cost_map_url(db, fixture_fetch, tmp_path) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    cost_map = write_cost_map(
        tmp_path, {"gpt-4-0613": {"litellm_provider": "openai", "deprecation_date": "2025-06-06"}}
    )
    patch_file = tmp_path / "patch.json"
    report = tmp_path / "drift.md"
    url = "https://example.test/model_prices.json"

    code, output = run(
        [
            "costmap-patch",
            "--db",
            str(db),
            "--cost-map",
            cost_map,
            "--out",
            str(patch_file),
            "--report",
            str(report),
            "--cost-map-url",
            url,
        ],
        fixture_fetch,
    )
    text = report.read_text(encoding="utf-8")

    assert code == 0
    assert f"Patch written to {patch_file}" in output
    assert f"Report written to {report}" in output
    assert text.splitlines()[0] == "# litellm cost map drift"
    assert f"Generated by sundial-llm against {url}." in text
    assert "1 entry differs from provider-published retirement dates." in text
    assert "| gpt-4-0613 | 2025-06-06 | 2026-10-23 | https://" in text
    assert json.loads(patch_file.read_text(encoding="utf-8")) == {
        "gpt-4-0613": {"deprecation_date": "2026-10-23"}
    }


def test_costmap_patch_writes_the_report_even_when_nothing_differs(db, fixture_fetch, tmp_path) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    cost_map = write_cost_map(
        tmp_path, {"gpt-4-0613": {"litellm_provider": "openai", "deprecation_date": "2026-10-23"}}
    )
    report = tmp_path / "drift.md"

    code, output = run(
        ["costmap-patch", "--db", str(db), "--cost-map", cost_map, "--report", str(report)], fixture_fetch
    )
    text = report.read_text(encoding="utf-8")

    assert code == 0
    assert f"Report written to {report}" in output
    assert text.splitlines()[0] == "# litellm cost map drift"
    assert "No entries differ from provider-published retirement dates." in text
    assert "## Patch" not in text
    assert (
        "Generated by sundial-llm against "
        "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json."
    ) in text


def test_costmap_patch_on_an_empty_store_writes_nothing_and_exits_1(db, fixture_fetch, tmp_path) -> None:
    cost_map = write_cost_map(tmp_path, {"gpt-4-0613": {"litellm_provider": "openai"}})
    patch_file = tmp_path / "patch.json"
    report = tmp_path / "drift.md"

    code, output = run(
        [
            "costmap-patch",
            "--db",
            str(db),
            "--cost-map",
            cost_map,
            "--out",
            str(patch_file),
            "--report",
            str(report),
        ],
        fixture_fetch,
    )

    assert code == 1
    assert "sundial: the registry is empty; run sundial scan or sundial import first" in output
    assert not patch_file.exists()
    assert not report.exists()


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


def test_export_from_an_empty_store_writes_nothing_and_exits_1(db, fixture_fetch, tmp_path) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    target = tmp_path / "lifecycle.json"
    run(["export", "--out", str(target), "--db", str(db)], fixture_fetch)
    published = target.read_bytes()
    empty = str(tmp_path / "empty.db")
    fresh = tmp_path / "fresh.json"

    code, output = run(["export", "--out", str(target), "--db", empty], fixture_fetch, now=T2)
    fresh_code, _ = run(["export", "--out", str(fresh), "--db", empty], fixture_fetch, now=T2)

    assert code == 1 and fresh_code == 1
    assert "sundial: the registry is empty; run sundial scan or sundial import first" in output
    assert target.read_bytes() == published
    assert not fresh.exists()


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


@pytest.mark.parametrize(
    "text",
    [None, "rules: {\n", "42\n", "ruels: []\n", "usage_retention_days: 0\n", "usage_retention_days: -1\n"],
    ids=["missing", "unparseable", "scalar", "invalid", "zero-retention", "negative-retention"],
)
def test_a_config_that_cannot_be_found_parsed_or_validated_exits_2(tmp_path, fixture_fetch, text) -> None:
    config = tmp_path / "sundial.yaml"
    if text is not None:
        config.write_text(text, encoding="utf-8")

    code, output = run(["status", "--config", str(config), "--db", str(tmp_path / "cli.db")], fixture_fetch)

    assert code == 2
    assert output.startswith("sundial: ")


SERVE_TOKEN = "s3cret-token"
INSTALL_HINT = 'sundial: sundial serve needs uvicorn; install it with: pip install "sundial-llm[serve]"'


def write_serve_config(tmp_path, db, *, usage_tracking: bool = False) -> str:
    """A config that never refreshes the registry in the background, so serving stays offline."""
    config = tmp_path / "serve.yaml"
    config.write_text(
        f"db: {db.as_posix()}\nregistry_url: null\nauto_scan_hours: 0\n"
        f"usage_tracking: {'true' if usage_tracking else 'false'}\n",
        encoding="utf-8",
    )
    return str(config)


@pytest.fixture
def serve_config(tmp_path, db) -> str:
    return write_serve_config(tmp_path, db)


def serve(args: list[str], fetch, env=None) -> tuple[int, str, list]:
    """Run `sundial serve` with a runner that records what it was asked to serve instead of serving."""
    served: list = []
    code, output = run(
        ["serve", *args], fetch, env=env, run_server=lambda app, host, port: served.append((app, host, port))
    )
    return code, output, served


async def decide(app, headers=None) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sundial.test"
    ) as client:
        return await client.post("/v1/decide", json={"model": "gpt-4-0613"}, headers=headers)


def test_serve_runs_the_decision_service_on_the_default_host_and_port(
    db, serve_config, fixture_fetch
) -> None:
    run(["scan", "--db", str(db)], fixture_fetch)
    tracked = len(Store(db).load_records())

    code, output, served = serve(["--config", serve_config], fixture_fetch)

    assert code == 0
    assert [(type(app), host, port) for app, host, port in served] == [(DecisionService, "127.0.0.1", 8765)]
    assert output == f"Serving lifecycle decisions on http://127.0.0.1:8765 ({tracked} records)\n"


@pytest.mark.parametrize(
    ("host", "url"),
    [
        ("0.0.0.0", "http://0.0.0.0:9000"),
        ("::", "http://[::]:9000"),
        ("sundial.internal", "http://sundial.internal:9000"),
    ],
    ids=["ipv4-any", "ipv6-any", "hostname"],
)
def test_serve_on_a_public_address_without_a_token_warns_and_still_serves(
    serve_config, fixture_fetch, host, url
) -> None:
    code, output, served = serve(["--config", serve_config, "--host", host, "--port", "9000"], fixture_fetch)

    lines = output.splitlines()
    assert code == 0
    assert [(served_host, port) for _, served_host, port in served] == [(host, 9000)]
    assert len(lines) == 2
    assert lines[0].startswith("warning: ") and "SUNDIAL_SERVE_TOKEN" in lines[0]
    assert lines[1] == f"Serving lifecycle decisions on {url} (0 records)"


@pytest.mark.anyio
async def test_serve_with_a_token_does_not_warn_and_the_service_requires_it(
    serve_config, fixture_fetch
) -> None:
    code, output, served = serve(
        ["--config", serve_config, "--host", "0.0.0.0"],
        fixture_fetch,
        env={"SUNDIAL_SERVE_TOKEN": SERVE_TOKEN},
    )
    app = served[0][0]

    anonymous = await decide(app)
    wrong = await decide(app, {"Authorization": "Bearer not-the-token"})
    authorized = await decide(app, {"Authorization": f"Bearer {SERVE_TOKEN}"})

    assert code == 0
    assert "warning" not in output
    assert (anonymous.status_code, wrong.status_code, authorized.status_code) == (401, 401, 200)
    assert authorized.json()["action"] == "allow"


@pytest.mark.anyio
@pytest.mark.parametrize("token", ["", "  \t "], ids=["empty", "whitespace"])
async def test_a_blank_token_means_no_token_so_a_public_address_warns(
    serve_config, fixture_fetch, token
) -> None:
    code, output, served = serve(
        ["--config", serve_config, "--host", "0.0.0.0"], fixture_fetch, env={"SUNDIAL_SERVE_TOKEN": token}
    )

    response = await decide(served[0][0])

    assert code == 0
    assert output.startswith("warning: ") and "SUNDIAL_SERVE_TOKEN" in output.splitlines()[0]
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("host", "bound", "url"),
    [
        ("localhost", "localhost", "http://localhost:8765"),
        ("LOCALHOST", "LOCALHOST", "http://LOCALHOST:8765"),
        ("127.0.0.2", "127.0.0.2", "http://127.0.0.2:8765"),
        ("::1", "::1", "http://[::1]:8765"),
        # The brackets belong to the URL, not the address, so the server binds the bare address.
        ("[::1]", "::1", "http://[::1]:8765"),
    ],
    ids=["localhost", "localhost-upper", "ipv4-loopback-range", "ipv6", "ipv6-bracketed"],
)
def test_serve_on_a_loopback_host_does_not_warn(serve_config, fixture_fetch, host, bound, url) -> None:
    code, output, served = serve(["--config", serve_config, "--host", host], fixture_fetch)

    assert code == 0
    assert [served_host for _, served_host, _ in served] == [bound]
    assert output == f"Serving lifecycle decisions on {url} (0 records)\n"


def test_serve_refreshes_the_registry_through_the_fetch_main_was_given(tmp_path, db) -> None:
    registry = "https://registry.sundial.test/lifecycle.json"
    config = tmp_path / "refresh.yaml"
    config.write_text(
        f"db: {db.as_posix()}\nregistry_url: {registry}\nauto_scan_hours: 24\nusage_tracking: false\n",
        encoding="utf-8",
    )
    requested: list[str] = []
    asked = threading.Event()

    def offline(url: str) -> str:
        requested.append(url)
        asked.set()
        raise OSError("offline")

    code, _, _ = serve(["--config", str(config)], offline)
    # The empty store is stale, so counting records at startup starts a background refresh.
    refreshed = asked.wait(10)
    for thread in threading.enumerate():
        if thread.name == "sundial-background":
            thread.join(10)

    assert code == 0
    assert refreshed
    assert requested[0] == registry


def test_serve_runs_uvicorn_when_no_runner_is_given(serve_config, fixture_fetch, monkeypatch) -> None:
    calls: list = []
    uvicorn = types.ModuleType("uvicorn")
    uvicorn.run = lambda app, **options: calls.append((app, options))
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)

    code, _ = run(["serve", "--config", serve_config, "--port", "9001"], fixture_fetch)

    assert code == 0
    assert [(type(app), options) for app, options in calls] == [
        (DecisionService, {"host": "127.0.0.1", "port": 9001, "log_level": "info"})
    ]


def test_serve_without_uvicorn_exits_2_with_the_install_hint(
    serve_config, fixture_fetch, monkeypatch
) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", None)

    code, output = run(["serve", "--config", serve_config], fixture_fetch)

    assert code == 2
    assert output == INSTALL_HINT + "\n"


@pytest.mark.parametrize("port", ["0", "70000"])
def test_serve_rejects_a_port_outside_1_to_65535(serve_config, fixture_fetch, capsys, port) -> None:
    with pytest.raises(SystemExit) as exited:
        serve(["--config", serve_config, "--port", port], fixture_fetch)

    assert exited.value.code == 2
    assert "65535" in capsys.readouterr().err


def test_serve_flushes_its_startup_lines_before_the_server_takes_over(serve_config, fixture_fetch) -> None:
    class Out(io.StringIO):
        flushed = ""

        def flush(self) -> None:
            self.flushed = self.getvalue()

    out = Out()
    seen: list[str] = []

    main(
        ["serve", "--config", serve_config, "--host", "0.0.0.0"],
        fetch=fixture_fetch,
        now=lambda: T1,
        post=lambda url, body: None,
        out=out,
        env={},
        run_server=lambda app, host, port: seen.append(out.flushed),
    )

    assert seen == [out.getvalue()]
    assert len(out.getvalue().splitlines()) == 2


@pytest.mark.anyio
async def test_serve_counts_usage_with_the_runtime_recorder(tmp_path, db, fixture_fetch, monkeypatch) -> None:
    runtimes: list = []

    def build(*args, **options):
        runtimes.append(build_runtime(*args, **options, at_exit=lambda close: None))
        return runtimes[-1]

    monkeypatch.setattr(cli, "build_runtime", build)
    config = write_serve_config(tmp_path, db, usage_tracking=True)
    _, _, served = serve(["--config", config], fixture_fetch)

    await decide(served[0][0])
    runtimes[0].recorder.close()

    rows = Store(db).usage_rows(T1.date())
    assert [(row.day, row.public_model, row.calls) for row in rows] == [(T1.date(), "gpt-4-0613", 1)]
