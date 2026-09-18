from datetime import date, datetime, timezone

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

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)

POLICY = load_policy(
    {
        "rules": [
            {"name": "block-retired", "when": {"state": "retired"}, "action": "block", "mode": "apply"},
            {"name": "redirect-week", "when": {"days_until_retirement_lte": 7}, "action": "redirect", "mode": "apply"},
            {"name": "warn-90", "when": {"days_until_retirement_lte": 90}, "action": "warn"},
        ],
        "successors": {"leaving": "next"},
    }
)


def rec(model_id: str, retire_on: date) -> LifecycleRecord:
    return LifecycleRecord(
        platform="openai", model_id=model_id, state=LifecycleState.DEPRECATED, retire_on=retire_on, source_url="u", evidence="e"
    )


RECORDS = {r.key: r for r in (rec("gpt-dead", date(2026, 1, 1)), rec("gpt-leaving", date(2026, 9, 22)), rec("gpt-fading", date(2026, 10, 19)))}


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
            {"litellm_params": {"model": "azure/prod"}, "model_info": {"base_model": "azure/gpt-4o-2024-05-13"}},
            {"litellm_params": {"model": "openai/gpt-4o"}},
        ],
    }
)


@pytest.fixture
def hook() -> SundialHook:
    guard = LifecycleGuard(lambda: RECORDS, LitellmRouterResolver(lambda: ROUTER), POLICY, clock=lambda: NOW)
    return SundialHook(guard)


def test_resolver_reads_the_upstream_model_and_base_model_of_every_deployment() -> None:
    resolved = LitellmRouterResolver(lambda: ROUTER).resolve("mixed")

    assert tuple(resolved) == (
        UpstreamModel("azure", "prod", "gpt-4o-2024-05-13"),
        UpstreamModel("openai", "gpt-4o"),
    )


def test_resolver_falls_back_to_the_public_name_for_unknown_groups_and_missing_routers() -> None:
    assert tuple(LitellmRouterResolver(lambda: ROUTER).resolve("openai/gpt-dead")) == (UpstreamModel("openai", "gpt-dead"),)
    assert tuple(LitellmRouterResolver(lambda: None).resolve("gpt-dead")) == (UpstreamModel("openai", "gpt-dead"),)


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
