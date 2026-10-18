from datetime import date, datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("litellm")
fastapi = pytest.importorskip("fastapi")

from litellm.integrations.custom_logger import CustomLogger  # noqa: E402

from sundial_llm.guard import LifecycleGuard  # noqa: E402
from sundial_llm.integrations import litellm_hook  # noqa: E402
from sundial_llm.integrations.litellm_hook import LitellmRouterResolver, SundialHook  # noqa: E402
from sundial_llm.matching import UpstreamModel  # noqa: E402
from sundial_llm.models import LifecycleRecord, LifecycleState  # noqa: E402
from sundial_llm.policy import load_policy  # noqa: E402
from sundial_llm.store import Store  # noqa: E402
from sundial_llm.usage import UsageRecorder, UsageRow  # noqa: E402

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)

POLICY = load_policy(
    {
        "rules": [
            {"name": "block-retired", "when": {"state": "retired"}, "action": "block", "mode": "apply"},
            {
                "name": "redirect-week",
                "when": {"days_until_retirement_lte": 7},
                "action": "redirect",
                "mode": "apply",
            },
            {"name": "warn-90", "when": {"days_until_retirement_lte": 90}, "action": "warn"},
        ],
        "successors": {"leaving": "next"},
    }
)


def rec(model_id: str, retire_on: date) -> LifecycleRecord:
    return LifecycleRecord(
        platform="openai",
        model_id=model_id,
        state=LifecycleState.DEPRECATED,
        retire_on=retire_on,
        source_url="u",
        evidence="e",
    )


RECORDS = {
    r.key: r
    for r in (
        rec("gpt-dead", date(2026, 1, 1)),
        rec("gpt-leaving", date(2026, 9, 22)),
        rec("gpt-fading", date(2026, 10, 19)),
    )
}


class FakeRouter:
    def __init__(self, groups: dict[str, list[dict]]) -> None:
        self.groups = groups

    def get_model_list(self, model_name: str | None = None) -> list[dict] | None:
        return self.groups.get(model_name or "")


ROUTER = FakeRouter(
    {
        "dead": [{"litellm_params": {"model": "openai/gpt-dead"}}],
        "leaving": [{"litellm_params": {"model": "openai/gpt-leaving"}}],
        "fading": [{"litellm_params": {"model": "openai/gpt-fading"}, "model_info": {}}],
        "mixed": [
            {
                "litellm_params": {"model": "azure/prod"},
                "model_info": {"base_model": "azure/gpt-4o-2024-05-13"},
            },
            {"litellm_params": {"model": "openai/gpt-4o"}},
        ],
    }
)


def router_guard() -> LifecycleGuard:
    return LifecycleGuard(lambda: RECORDS, LitellmRouterResolver(lambda: ROUTER), POLICY, clock=lambda: NOW)


@pytest.fixture
def hook() -> SundialHook:
    return SundialHook(router_guard())


@pytest.fixture
def usage(tmp_path) -> Store:
    return Store(tmp_path / "usage.db")


@pytest.fixture
def recorder(usage: Store) -> UsageRecorder:
    return UsageRecorder(usage, clock=lambda: NOW)


class TeamAuth:
    team_id = "team-a"


def test_resolver_reads_the_upstream_model_and_base_model_of_every_deployment() -> None:
    resolved = LitellmRouterResolver(lambda: ROUTER).resolve("mixed")

    assert tuple(resolved) == (
        UpstreamModel("azure", "prod", "gpt-4o-2024-05-13"),
        UpstreamModel("openai", "gpt-4o"),
    )


def test_resolver_falls_back_to_the_public_name_for_unknown_groups_and_missing_routers() -> None:
    assert tuple(LitellmRouterResolver(lambda: ROUTER).resolve("openai/gpt-dead")) == (
        UpstreamModel("openai", "gpt-dead"),
    )
    assert tuple(LitellmRouterResolver(lambda: None).resolve("gpt-dead")) == (
        UpstreamModel("openai", "gpt-dead"),
    )


def test_hook_is_a_litellm_custom_logger_and_the_module_exposes_an_instance(hook: SundialHook) -> None:
    assert isinstance(hook, CustomLogger)
    assert isinstance(litellm_hook.sundial_hook, SundialHook)


async def test_retired_model_group_is_refused_with_410(hook: SundialHook) -> None:
    with pytest.raises(fastapi.HTTPException) as refused:
        await hook.async_pre_call_hook(None, None, {"model": "dead"}, "acompletion")

    assert refused.value.status_code == 410
    assert "dead" in str(refused.value.detail) and "2026-01-01" in str(refused.value.detail)


async def test_redirect_rewrites_the_requested_model_group(hook: SundialHook) -> None:
    data = await hook.async_pre_call_hook(None, None, {"model": "leaving", "messages": []}, "acompletion")

    assert data["model"] == "next"


async def test_warned_request_is_left_alone(hook: SundialHook) -> None:
    data = await hook.async_pre_call_hook(None, None, {"model": "fading"}, "acompletion")

    assert data["model"] == "fading"


async def test_headers_hook_reports_lifecycle_for_a_warned_model(hook: SundialHook) -> None:
    data = await hook.async_pre_call_hook(None, None, {"model": "fading"}, "acompletion")

    headers = await hook.async_post_call_response_headers_hook(data, None, object())

    assert headers is not None
    assert headers["Sunset"] == "Mon, 19 Oct 2026 00:00:00 GMT"
    assert headers["X-Sundial-Rule"] == "warn-90"


async def test_headers_survive_a_redirect_and_say_where_the_request_came_from(hook: SundialHook) -> None:
    data = await hook.async_pre_call_hook(None, None, {"model": "leaving"}, "acompletion")

    headers = await hook.async_post_call_response_headers_hook(data, None, object())

    assert headers is not None
    assert headers["X-Sundial-Redirected-From"] == "leaving"
    assert headers["X-Sundial-Successor"] == "next"


async def test_healthy_model_gets_no_headers(hook: SundialHook) -> None:
    data = await hook.async_pre_call_hook(None, None, {"model": "openai/gpt-fine"}, "acompletion")

    assert await hook.async_post_call_response_headers_hook(data, None, object()) is None


async def test_headers_hook_works_without_a_prior_pre_call(hook: SundialHook) -> None:
    headers = await hook.async_post_call_response_headers_hook({"model": "fading"}, None, None)

    assert headers is not None and headers["X-Sundial-Rule"] == "warn-90"


async def test_request_without_a_model_is_ignored(hook: SundialHook) -> None:
    assert await hook.async_pre_call_hook(None, None, {"input": "x"}, "aembedding") == {"input": "x"}


async def test_internal_failure_never_breaks_the_request() -> None:
    class Exploding:
        def decide(self, public_model: str):
            raise RuntimeError("boom")

    hook = SundialHook(Exploding())

    assert await hook.async_pre_call_hook(None, None, {"model": "dead"}, "acompletion") == {"model": "dead"}
    assert await hook.async_post_call_response_headers_hook({"model": "dead"}, None, None) is None


async def test_pre_call_buffers_one_call_for_the_public_model_team_and_matched_record(
    usage: Store, recorder: UsageRecorder
) -> None:
    hook = SundialHook(router_guard(), recorder=recorder)

    await hook.async_pre_call_hook(TeamAuth(), None, {"model": "fading"}, "acompletion")

    assert usage.usage_rows(NOW.date()) == []  # still buffered in memory
    recorder.flush()
    assert usage.usage_rows(NOW.date()) == [
        UsageRow(NOW.date(), "fading", "team-a", "openai", "gpt-fading", "", 1)
    ]


async def test_a_request_without_an_auth_object_records_an_empty_team(
    usage: Store, recorder: UsageRecorder
) -> None:
    hook = SundialHook(router_guard(), recorder=recorder)

    await hook.async_pre_call_hook(None, None, {"model": "fading"}, "acompletion")
    recorder.flush()

    assert [row.team for row in usage.usage_rows(NOW.date())] == [""]


async def test_redirected_and_allowed_requests_are_recorded_under_the_name_the_client_sent(
    usage: Store, recorder: UsageRecorder
) -> None:
    hook = SundialHook(router_guard(), recorder=recorder)

    redirected = await hook.async_pre_call_hook(None, None, {"model": "leaving"}, "acompletion")
    await hook.async_pre_call_hook(None, None, {"model": "openai/gpt-fine"}, "acompletion")
    recorder.flush()

    assert redirected["model"] == "next"
    assert usage.usage_rows(NOW.date()) == [
        UsageRow(NOW.date(), "leaving", "", "openai", "gpt-leaving", "", 1),
        UsageRow(NOW.date(), "openai/gpt-fine", "", "", "", "", 1),
    ]


class ListRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def record(self, public_model, team, key) -> None:
        self.calls.append((public_model, team, key))


def offline_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point SUNDIAL_CONFIG at a config that never touches the network; return its database path."""
    db = tmp_path / "wired.db"
    config = tmp_path / "sundial.yaml"
    config.write_text(f"db: {db.as_posix()}\nregistry_url: null\nauto_scan_hours: 0\n", encoding="utf-8")
    monkeypatch.setenv("SUNDIAL_CONFIG", str(config))
    monkeypatch.delenv("SUNDIAL_DB", raising=False)
    return db


async def test_a_hook_with_no_arguments_records_usage_through_the_runtime_it_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = offline_config(tmp_path, monkeypatch)
    hook = SundialHook()

    data = await hook.async_pre_call_hook(TeamAuth(), None, {"model": "gpt-wired"}, "acompletion")
    adopted = hook._recorder
    assert isinstance(adopted, UsageRecorder)
    adopted.close()

    assert data["model"] == "gpt-wired"
    assert [(row.public_model, row.team, row.calls) for row in Store(db).usage_rows(date.min)] == [
        ("gpt-wired", "team-a", 1)
    ]


async def test_an_explicit_recorder_is_kept_when_the_guard_is_built_lazily(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = offline_config(tmp_path, monkeypatch)
    explicit = ListRecorder()
    hook = SundialHook(recorder=explicit)

    await hook.async_pre_call_hook(TeamAuth(), None, {"model": "gpt-wired"}, "acompletion")

    assert hook._recorder is explicit
    assert explicit.calls == [("gpt-wired", "team-a", None)]
    assert Store(db).usage_rows(date.min) == []


async def test_a_recorder_that_raises_never_breaks_the_request_or_the_decision() -> None:
    class Exploding:
        def record(self, public_model, team, key) -> None:
            raise RuntimeError("boom")

    hook = SundialHook(router_guard(), recorder=Exploding())

    data = await hook.async_pre_call_hook(None, None, {"model": "leaving"}, "acompletion")
    assert data["model"] == "next"
    with pytest.raises(fastapi.HTTPException):
        await hook.async_pre_call_hook(None, None, {"model": "dead"}, "acompletion")
