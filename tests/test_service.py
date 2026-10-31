import copy
import json
import logging
from datetime import date, datetime, timezone

import httpx
import pytest

from sundial_llm.guard import LifecycleGuard
from sundial_llm.integrations.service import PORTKEY_PROVIDERS, DecisionService
from sundial_llm.models import LifecycleRecord, LifecycleState
from sundial_llm.policy import ALLOW, Action, Decision, Mode, load_policy
from sundial_llm.store import Store
from sundial_llm.usage import UsageRecorder

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
DECIDE = "/v1/decide"
PORTKEY = "/portkey/before-request"

POLICY = load_policy(
    {
        "rules": [
            {"name": "block-retired", "when": {"state": "retired"}, "action": "block", "mode": "apply"},
            {
                "name": "redirect-final-week",
                "when": {"days_until_retirement_lte": 7},
                "action": "redirect",
                "mode": "apply",
            },
            {"name": "warn-90", "when": {"days_until_retirement_lte": 90}, "action": "warn"},
        ],
        "successors": {"gpt-leaving": "gpt-next", "gpt-4o-mini": "gpt-4.1-mini"},
    }
)


def rec(model_id: str, retire_on: date, **extra: object) -> LifecycleRecord:
    base = {"platform": "openai", "state": LifecycleState.DEPRECATED, "source_url": "u", "evidence": "e"}
    return LifecycleRecord(model_id=model_id, retire_on=retire_on, **{**base, **extra})


DEAD = rec("gpt-dead", date(2026, 1, 1), replacements=("gpt-next",))
LEAVING = rec("gpt-leaving", date(2026, 9, 22))
FADING = rec("gpt-fading", date(2026, 10, 19), deprecated_on=date(2026, 4, 1), replacements=("gpt-next",))
AZURE_RETIRED = rec("gpt-4o", date(2026, 9, 1), platform="azure", version="2024-05-13")
AZURE_CURRENT = rec(
    "gpt-4o", date(2027, 3, 1), platform="azure", version="2024-11-20", state=LifecycleState.ACTIVE
)
# Only one Azure version of each of these, so matching by name alone is exact.
AZURE_SOLO_RETIRED = rec("gpt-35-turbo", date(2026, 6, 1), platform="azure", version="0613")
AZURE_SOLO_LEAVING = rec("gpt-4o-mini", date(2026, 9, 22), platform="azure", version="2024-07-18")
GEMINI_DEAD = rec("gemini-old", date(2026, 6, 1), platform="gemini")
RECORDS = {
    r.key: r
    for r in (
        DEAD,
        LEAVING,
        FADING,
        AZURE_RETIRED,
        AZURE_CURRENT,
        AZURE_SOLO_RETIRED,
        AZURE_SOLO_LEAVING,
        GEMINI_DEAD,
    )
}

FADING_MESSAGE = "Model 'gpt-fading' is deprecated and retires on 2026-10-19 (Sundial rule 'warn-90'). Use gpt-next instead."
DEAD_MESSAGE = (
    "Model 'gpt-dead' was retired on 2026-01-01 (Sundial rule 'block-retired'). Use gpt-next instead."
)


class NoResolver:
    """The service names its upstreams, so the guard's resolver must never be asked."""

    def resolve(self, public_model: str):
        raise AssertionError("the decision service must pass upstreams explicitly")


def make_guard() -> LifecycleGuard:
    return LifecycleGuard(lambda: RECORDS, NoResolver(), POLICY, clock=lambda: NOW)


class ListRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def record(self, public_model, team, key) -> None:
        self.calls.append((public_model, team, key))


class ExplodingRecorder:
    def record(self, public_model, team, key) -> None:
        raise RuntimeError("boom")


class ExplodingDecider:
    def decide(self, public_model, upstreams=None):
        raise RuntimeError("boom")


class SpyDecider:
    """A decider with no record_count, which allows everything and remembers what it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def decide(self, public_model, upstreams=None):
        self.calls.append((public_model, upstreams))
        return ALLOW


class BrokenCountDecider(SpyDecider):
    def record_count(self) -> int:
        raise RuntimeError("db locked")


class Opaque:
    """A value json cannot encode."""


class UnencodableDecider:
    """Answers an enforced redirect whose rule json cannot encode, so only encoding the answer fails.
    It stands in for any answer that parses and decides but cannot be serialized, such as a body
    nested just deep enough that the redirect envelope passes the encoder's recursion limit."""

    def decide(self, public_model, upstreams=None):
        return Decision(
            action=Action.REDIRECT, mode=Mode.APPLY, enforced=True, rule=Opaque(), target="gpt-next"
        )


def client_for(app: DecisionService) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sundial.test")


def service(guard=None, recorder=None, **kwargs) -> httpx.AsyncClient:
    return client_for(DecisionService(guard or make_guard(), recorder, **kwargs))


def portkey_context(model: str = "gpt-fading", provider: str = "openai", **overrides: object) -> dict:
    """The body Portkey's webhook guardrail posts: the plugin context, without request headers."""
    context = {
        "request": {
            "json": {
                "model": model,
                "messages": [{"role": "user", "content": "héllo"}],
                "temperature": 0.2,
                "metadata": {"trace": ["a", "b"]},
            },
            "text": "héllo",
            "isStreamingRequest": False,
            "isTransformed": False,
        },
        "response": {"json": {}, "text": "", "statusCode": None},
        "provider": provider,
        "requestType": "chatComplete",
        "metadata": {"team": "payments"},
        "eventType": "beforeRequestHook",
    }
    context.update(overrides)
    return context


async def call(app: DecisionService, scope: dict, receive) -> list[dict]:
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def http_scope(method: str, path: str, headers: list | None = None) -> dict:
    return {"type": "http", "method": method, "path": path, "headers": headers or []}


# --- health ---------------------------------------------------------------------------------------


async def test_healthz_on_a_fresh_service_reports_the_real_record_count() -> None:
    async with service() as client:
        first = await client.get("/healthz")
        await client.post(DECIDE, json={"model": "gpt-fading"})
        after = await client.get("/healthz")

    assert (first.status_code, first.json()) == (200, {"status": "ok", "records": len(RECORDS)})
    assert (after.status_code, after.json()) == (200, {"status": "ok", "records": len(RECORDS)})


async def test_healthz_stays_open_when_a_token_is_configured() -> None:
    async with service(token="s3cret") as client:
        response = await client.get("/healthz")

    assert (response.status_code, response.json()) == (200, {"status": "ok", "records": len(RECORDS)})


async def test_healthz_reports_no_records_for_a_decider_that_cannot_count_them() -> None:
    async with service(SpyDecider()) as client:
        response = await client.get("/healthz")

    assert response.json() == {"status": "ok", "records": 0}


async def test_healthz_reports_no_records_and_logs_quietly_when_counting_fails(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="sundial")

    async with service(BrokenCountDecider()) as client:
        response = await client.get("/healthz")

    assert (response.status_code, response.json()) == (200, {"status": "ok", "records": 0})
    assert caplog.records
    assert {record.levelno for record in caplog.records} == {logging.DEBUG}


# --- generic contract -----------------------------------------------------------------------------


async def test_decide_warns_with_the_message_and_headers_a_gateway_needs() -> None:
    async with service() as client:
        response = await client.post(DECIDE, json={"model": "gpt-fading", "team": "payments"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-length"] == str(len(response.content))
    assert response.json() == {
        "action": "warn",
        "enforced": False,
        "block": False,
        "redirect_to": None,
        "state": "deprecated",
        "retire_on": "2026-10-19",
        "days_left": 30,
        "replacements": ["gpt-next"],
        "rule": "warn-90",
        "approximate": False,
        "message": FADING_MESSAGE,
        "headers": {
            "Deprecation": "@1775001600",
            "Sunset": "Mon, 19 Oct 2026 00:00:00 GMT",
            "X-Sundial-State": "deprecated",
            "X-Sundial-Successor": "gpt-next",
            "X-Sundial-Rule": "warn-90",
            "X-Sundial-Days-Left": "30",
        },
    }


async def test_decide_sets_block_only_for_an_enforced_block() -> None:
    async with service() as client:
        enforced = (await client.post(DECIDE, json={"model": "gpt-dead"})).json()
        # Vertex matches Gemini records approximately, so the same rule cannot be enforced.
        approximate = (
            await client.post(DECIDE, json={"model": "gemini-old", "provider": "vertex_ai"})
        ).json()

    assert (enforced["action"], enforced["enforced"], enforced["block"]) == ("block", True, True)
    assert enforced["redirect_to"] is None
    assert enforced["message"] == DEAD_MESSAGE
    assert (approximate["action"], approximate["enforced"], approximate["block"]) == ("block", False, False)
    assert approximate["approximate"] is True


async def test_decide_names_the_successor_only_for_an_enforced_redirect() -> None:
    async with service() as client:
        body = (await client.post(DECIDE, json={"model": "gpt-leaving"})).json()

    assert (body["action"], body["enforced"], body["block"]) == ("redirect", True, False)
    assert body["redirect_to"] == "gpt-next"
    assert body["headers"]["X-Sundial-Successor"] == "gpt-next"
    assert body["headers"]["X-Sundial-Redirected-From"] == "gpt-leaving"


async def test_decide_matches_a_versioned_azure_record_exactly_through_base_model() -> None:
    async with service() as client:
        body = (
            await client.post(
                DECIDE, json={"model": "prod-gpt4o", "provider": "azure", "base_model": "gpt-4o-2024-05-13"}
            )
        ).json()

    assert (body["action"], body["enforced"], body["block"]) == ("block", True, True)
    assert (body["approximate"], body["state"], body["retire_on"]) == (False, "retired", "2026-09-01")


async def test_decide_keeps_exact_matching_for_a_single_azure_version() -> None:
    # Only the Portkey route distrusts Azure model names; the generic caller names the upstream.
    async with service() as client:
        body = (await client.post(DECIDE, json={"model": "gpt-35-turbo", "provider": "azure"})).json()

    assert (body["action"], body["enforced"], body["block"], body["approximate"]) == (
        "block",
        True,
        True,
        False,
    )


async def test_decide_reads_a_model_without_a_provider_the_litellm_way() -> None:
    async with service() as client:
        prefixed = (await client.post(DECIDE, json={"model": "openai/gpt-dead"})).json()
        explicit_nulls = (
            await client.post(
                DECIDE, json={"model": "gpt-dead", "provider": None, "base_model": None, "team": None}
            )
        ).json()

    assert prefixed["block"] is True
    assert explicit_nulls["block"] is True


async def test_decide_allows_a_provider_sundial_does_not_track() -> None:
    async with service() as client:
        response = await client.post(DECIDE, json={"model": "gpt-dead", "provider": "cohere"})

    assert response.status_code == 200
    assert response.json() == {
        "action": "allow",
        "enforced": False,
        "block": False,
        "redirect_to": None,
        "state": None,
        "retire_on": None,
        "days_left": None,
        "replacements": [],
        "rule": None,
        "approximate": False,
        "message": None,
        "headers": {},
    }


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"{}", "model"),
        (b'{"model": ""}', "model"),
        (b'{"model": 5}', "model"),
        (b"not json at all", "JSON"),
        (b"\xff\xfe\xfd", "JSON"),
        (b'["gpt-dead"]', "object"),
        (b'"gpt-dead"', "object"),
        (b'{"model": "gpt-dead", "provider": 5}', "provider"),
        (b'{"model": "gpt-dead", "base_model": ["gpt-4o"]}', "base_model"),
        (b'{"model": "gpt-dead", "team": {"id": 1}}', "team"),
    ],
)
async def test_decide_rejects_a_malformed_request_with_400(content: bytes, reason: str) -> None:
    recorder = ListRecorder()
    async with service(recorder=recorder) as client:
        response = await client.post(DECIDE, content=content, headers={"content-type": "application/json"})

    assert response.status_code == 400
    assert reason in response.json()["error"]
    assert recorder.calls == []


async def test_decide_refuses_an_oversized_body_with_413() -> None:
    async with service(max_body_bytes=64) as client:
        response = await client.post(DECIDE, json={"model": "gpt-dead", "padding": "x" * 100})

    assert response.status_code == 413
    assert "64" in response.json()["error"]


async def test_a_decider_that_raises_answers_200_allow_with_an_error() -> None:
    async with service(ExplodingDecider()) as client:
        response = await client.post(DECIDE, json={"model": "gpt-dead"})

    assert response.status_code == 200
    body = response.json()
    assert (body["action"], body["block"], body["redirect_to"]) == ("allow", False, None)
    assert body["error"]


async def test_an_answer_that_cannot_be_encoded_still_allows_on_the_generic_route() -> None:
    async with service(UnencodableDecider()) as client:
        response = await client.post(DECIDE, json={"model": "gpt-leaving"})

    assert response.status_code == 200
    assert response.headers["content-length"] == str(len(response.content))
    body = response.json()
    assert (body["action"], body["block"], body["redirect_to"]) == ("allow", False, None)
    assert body["error"]


# --- Portkey webhook guardrail --------------------------------------------------------------------


def test_the_portkey_slug_map_is_exactly_the_documented_one() -> None:
    assert PORTKEY_PROVIDERS == {
        "openai": "openai",
        "anthropic": "anthropic",
        "azure-openai": "azure",
        "azure-ai": "azure_ai",
        "bedrock": "bedrock",
        "google": "gemini",
        "vertex-ai": "vertex_ai",
    }


async def test_portkey_refuses_an_enforced_block_with_the_message_in_data() -> None:
    async with service() as client:
        response = await client.post(PORTKEY, json=portkey_context("gpt-dead"))

    assert response.status_code == 200
    assert response.json() == {
        "verdict": False,
        "data": {
            "action": "block",
            "enforced": True,
            "state": "retired",
            "retire_on": "2026-01-01",
            "days_left": -261,
            "replacements": ["gpt-next"],
            "rule": "block-retired",
            "approximate": False,
            "message": DEAD_MESSAGE,
        },
    }


async def test_portkey_redirect_replaces_only_the_model_in_the_request_body() -> None:
    context = portkey_context("gpt-leaving")
    original = copy.deepcopy(context)

    async with service() as client:
        answer = (await client.post(PORTKEY, json=context)).json()

    assert answer["verdict"] is True
    assert answer["transformedData"] == {
        "request": {"json": {**original["request"]["json"], "model": "gpt-next"}}
    }
    data = answer["data"]
    assert (data["action"], data["enforced"]) == ("redirect", True)
    assert (data["redirected_from"], data["target"]) == ("gpt-leaving", "gpt-next")


def test_portkey_redirect_never_mutates_the_parsed_context() -> None:
    # Over HTTP the service only ever sees bytes, so the parsed context is checked directly.
    context = portkey_context("gpt-leaving")
    original = copy.deepcopy(context)

    answer = DecisionService(make_guard())._portkey(context)

    assert answer["transformedData"]["request"]["json"]["model"] == "gpt-next"
    assert context == original


@pytest.mark.parametrize(
    ("slug", "model", "action"),
    [
        ("azure-openai", "gpt-35-turbo", "block"),
        ("azure-openai", "gpt-4o-mini", "redirect"),
        ("azure-ai", "gpt-35-turbo", "block"),
        ("azure-ai", "gpt-4o-mini", "redirect"),
        ("azure-openai", "gpt-4o-2024-05-13", "block"),
    ],
    ids=["openai-block", "openai-redirect", "ai-block", "ai-redirect", "versioned-body-model"],
)
async def test_portkey_never_enforces_azure_because_it_routes_by_deployment(
    slug: str, model: str, action: str
) -> None:
    async with service() as client:
        answer = (await client.post(PORTKEY, json=portkey_context(model, slug))).json()

    assert answer["verdict"] is True
    assert "transformedData" not in answer
    data = answer["data"]
    assert (data["action"], data["enforced"], data["approximate"]) == (action, False, True)
    assert "redirected_from" not in data


async def test_portkey_azure_allow_stays_a_bare_verdict() -> None:
    async with service() as client:
        answer = (await client.post(PORTKEY, json=portkey_context("gpt-4o", "azure-openai"))).json()

    assert answer == {"verdict": True}


async def test_an_answer_that_cannot_be_encoded_still_allows_on_the_portkey_route() -> None:
    async with service(UnencodableDecider()) as client:
        response = await client.post(PORTKEY, json=portkey_context("gpt-leaving"))

    assert response.status_code == 200
    assert response.headers["content-length"] == str(len(response.content))
    answer = response.json()
    assert answer["verdict"] is True and answer["data"]["explanation"]
    assert "transformedData" not in answer


@pytest.mark.parametrize(
    ("model", "provider", "slug"),
    [
        ("gpt-dead", "openai", "openai"),
        ("gpt-leaving", "openai", "openai"),
        ("gpt-fading", "openai", "openai"),
        ("gpt-fine", "openai", "openai"),
        ("gemini-old", "gemini", "google"),
        ("gemini-old", "vertex_ai", "vertex-ai"),
    ],
)
async def test_the_generic_block_flag_and_the_portkey_verdict_agree(
    model: str, provider: str, slug: str
) -> None:
    async with service() as client:
        generic = (await client.post(DECIDE, json={"model": model, "provider": provider})).json()
        portkey = (await client.post(PORTKEY, json=portkey_context(model, slug))).json()

    assert generic["block"] is (portkey["verdict"] is False)


async def test_portkey_lets_a_warned_request_through_with_a_summary() -> None:
    async with service() as client:
        answer = (await client.post(PORTKEY, json=portkey_context("gpt-fading"))).json()

    assert answer == {
        "verdict": True,
        "data": {
            "action": "warn",
            "enforced": False,
            "state": "deprecated",
            "retire_on": "2026-10-19",
            "days_left": 30,
            "replacements": ["gpt-next"],
            "rule": "warn-90",
            "approximate": False,
            "message": FADING_MESSAGE,
        },
    }


async def test_portkey_lets_an_untracked_model_through_with_a_bare_verdict() -> None:
    async with service() as client:
        answer = (await client.post(PORTKEY, json=portkey_context("gpt-fine"))).json()

    assert answer == {"verdict": True}


async def test_portkey_treats_a_provider_it_cannot_map_as_untracked() -> None:
    recorder = ListRecorder()

    async with service(recorder=recorder) as client:
        unmapped = (await client.post(PORTKEY, json=portkey_context("gpt-dead", "cohere"))).json()
        missing = (await client.post(PORTKEY, json=portkey_context("gpt-dead", provider=None))).json()
        unhashable = (await client.post(PORTKEY, json=portkey_context("gpt-dead", ["azure-openai"]))).json()

    for answer in (unmapped, missing, unhashable):
        assert answer["verdict"] is True
        assert answer["data"]["explanation"]
        assert "transformedData" not in answer
    assert "cohere" in unmapped["data"]["explanation"]
    assert recorder.calls == [("gpt-dead", "payments", None)] * 3


async def test_portkey_does_not_decide_after_the_response() -> None:
    decider = SpyDecider()
    recorder = ListRecorder()

    async with service(decider, recorder) as client:
        response = await client.post(PORTKEY, json=portkey_context("gpt-dead", eventType="afterRequestHook"))

    assert response.status_code == 200
    answer = response.json()
    assert answer["verdict"] is True and answer["data"]["explanation"]
    assert decider.calls == []
    assert recorder.calls == []


@pytest.mark.parametrize(
    "content",
    [
        json.dumps({**portkey_context(), "request": {"json": {"messages": []}}}).encode(),
        json.dumps({**portkey_context(), "request": {"json": "not an object"}}).encode(),
        json.dumps({"eventType": "beforeRequestHook"}).encode(),
        b"not json at all",
        b'["not", "an", "object"]',
        json.dumps(portkey_context("gpt-dead", padding="x" * 2000)).encode(),
    ],
    ids=["no-model", "json-not-object", "no-request", "bad-json", "not-an-object", "oversized"],
)
async def test_portkey_lets_a_request_it_cannot_read_through_with_an_explanation(content: bytes) -> None:
    async with service(max_body_bytes=1024) as client:
        response = await client.post(PORTKEY, content=content, headers={"content-type": "application/json"})

    assert response.status_code == 200
    answer = response.json()
    assert answer["verdict"] is True
    assert answer["data"]["explanation"]


async def test_portkey_google_reads_the_gemini_record() -> None:
    async with service() as client:
        answer = (await client.post(PORTKEY, json=portkey_context("gemini-old", "google"))).json()

    assert answer["verdict"] is False
    assert (answer["data"]["state"], answer["data"]["approximate"]) == ("retired", False)


async def test_portkey_vertex_matches_approximately_and_never_enforces() -> None:
    async with service() as client:
        answer = (await client.post(PORTKEY, json=portkey_context("gemini-old", "vertex-ai"))).json()

    assert answer["verdict"] is True
    assert "transformedData" not in answer
    data = answer["data"]
    assert (data["action"], data["enforced"], data["approximate"]) == ("block", False, True)


async def test_portkey_lets_the_request_through_when_the_decider_raises() -> None:
    async with service(ExplodingDecider()) as client:
        response = await client.post(PORTKEY, json=portkey_context("gpt-dead"))

    assert response.status_code == 200
    answer = response.json()
    assert answer["verdict"] is True and answer["data"]["explanation"]


# --- usage ----------------------------------------------------------------------------------------


async def test_both_routes_record_usage_under_the_model_the_caller_sent() -> None:
    recorder = ListRecorder()

    async with service(recorder=recorder) as client:
        await client.post(DECIDE, json={"model": "gpt-fading", "team": "payments"})
        await client.post(DECIDE, json={"model": "gpt-fine"})
        await client.post(
            DECIDE, json={"model": "prod-gpt4o", "provider": "azure", "base_model": "gpt-4o-2024-05-13"}
        )
        await client.post(PORTKEY, json=portkey_context("gpt-leaving"))
        await client.post(PORTKEY, json=portkey_context("gpt-fine", metadata={"team": 7}))
        await client.post(PORTKEY, json=portkey_context("gpt-dead", "cohere", metadata=None))

    assert recorder.calls == [
        ("gpt-fading", "payments", FADING.key),
        ("gpt-fine", None, None),
        ("prod-gpt4o", None, AZURE_RETIRED.key),
        ("gpt-leaving", "payments", LEAVING.key),
        ("gpt-fine", None, None),
        ("gpt-dead", None, None),
    ]


def inline(job) -> None:
    job()


@pytest.fixture
def usage_store(tmp_path) -> Store:
    return Store(tmp_path / "usage.db")


@pytest.mark.parametrize("path", [DECIDE, PORTKEY], ids=["decide", "portkey"])
async def test_a_4_mib_team_is_stored_as_no_team(usage_store: Store, path: str) -> None:
    recorder = UsageRecorder(usage_store, clock=lambda: NOW, spawn=inline)
    team = "t" * (4 * 1024 * 1024)
    payload = (
        {"model": "gpt-fading", "team": team}
        if path == DECIDE
        else portkey_context("gpt-fading", metadata={"team": team})
    )

    async with service(recorder=recorder) as client:
        response = await client.post(path, json=payload)
    recorder.close()

    assert response.status_code == 200
    rows = usage_store.usage_rows(NOW.date())
    assert [(r.public_model, r.team, r.calls) for r in rows] == [("gpt-fading", "", 1)]
    assert usage_store.path.stat().st_size < 1024 * 1024


async def test_aliases_of_a_tracked_model_beyond_the_daily_cap_are_counted_under_other(
    usage_store: Store,
) -> None:
    recorder = UsageRecorder(usage_store, clock=lambda: NOW, spawn=inline, max_tracked_names=2)

    async with service(recorder=recorder) as client:
        for number in range(5):
            await client.post(
                DECIDE,
                json={"model": f"alias-{number}", "provider": "azure", "base_model": "gpt-4o-2024-05-13"},
            )
    recorder.close()

    rows = usage_store.usage_rows(NOW.date())
    assert {(r.public_model, (r.platform, r.model_id, r.version), r.calls) for r in rows} == {
        ("alias-0", AZURE_RETIRED.key, 1),
        ("alias-1", AZURE_RETIRED.key, 1),
        ("(other)", AZURE_RETIRED.key, 3),
    }


async def test_a_recorder_that_raises_changes_no_response() -> None:
    requests = [
        (DECIDE, {"model": "gpt-dead"}),
        (DECIDE, {"model": "gpt-leaving", "team": "payments"}),
        (PORTKEY, portkey_context("gpt-dead")),
        (PORTKEY, portkey_context("gpt-leaving")),
        (PORTKEY, portkey_context("gpt-fine")),
    ]

    async with service() as plain, service(recorder=ExplodingRecorder()) as failing:
        for path, payload in requests:
            expected = await plain.post(path, json=payload)
            actual = await failing.post(path, json=payload)
            assert (actual.status_code, actual.json()) == (expected.status_code, expected.json()), path


# --- auth -----------------------------------------------------------------------------------------

POST_ROUTES = [(DECIDE, {"model": "gpt-fading"}), (PORTKEY, portkey_context())]


@pytest.mark.parametrize(("path", "payload"), POST_ROUTES, ids=["decide", "portkey"])
async def test_a_configured_token_is_required_on_every_post_route(path: str, payload: dict) -> None:
    async with service(token="s3cret") as client:
        missing = await client.post(path, json=payload)
        wrong = await client.post(path, json=payload, headers={"authorization": "Bearer nope"})
        wrong_scheme = await client.post(path, json=payload, headers={"authorization": "Basic s3cret"})
        right = await client.post(path, json=payload, headers={"authorization": "Bearer s3cret"})

    for refused in (missing, wrong, wrong_scheme):
        assert (refused.status_code, refused.json()) == (401, {"error": "unauthorized"})
    assert right.status_code == 200


@pytest.mark.parametrize(("path", "payload"), POST_ROUTES, ids=["decide", "portkey"])
async def test_without_a_token_no_authorization_header_is_needed(path: str, payload: dict) -> None:
    async with service() as client:
        response = await client.post(path, json=payload)

    assert response.status_code == 200


async def test_an_unauthorized_request_is_refused_before_its_body_is_read() -> None:
    async def receive() -> dict:
        raise AssertionError("the body was read before the token was checked")

    sent = await call(DecisionService(make_guard(), token="s3cret"), http_scope("POST", DECIDE), receive)

    assert sent[0]["status"] == 401


# --- transport ------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", [DECIDE, PORTKEY], ids=["decide", "portkey"])
@pytest.mark.parametrize(
    "messages",
    [
        [{"type": "http.disconnect"}],
        [
            {"type": "http.request", "body": b'{"model": "gpt-', "more_body": True},
            {"type": "http.disconnect"},
        ],
    ],
    ids=["before-any-body", "mid-body"],
)
async def test_a_client_that_disconnects_before_its_body_ends_gets_no_answer(
    path: str, messages: list
) -> None:
    pending = iter(messages)
    decider = SpyDecider()
    recorder = ListRecorder()

    async def receive() -> dict:
        return next(pending)

    sent = await call(DecisionService(decider, recorder), http_scope("POST", path), receive)

    assert sent == []
    assert (decider.calls, recorder.calls) == ([], [])


async def test_an_oversized_body_is_abandoned_as_soon_as_it_passes_the_limit() -> None:
    chunks = [b'{"model": "', b"x" * 10, b"x" * 10, b'"}']
    reads = 0

    async def receive() -> dict:
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": chunks[reads - 1], "more_body": reads < len(chunks)}

    sent = await call(DecisionService(make_guard(), max_body_bytes=15), http_scope("POST", DECIDE), receive)

    assert reads == 2
    assert sent[0]["status"] == 413


async def test_unknown_paths_get_404_and_wrong_methods_get_405() -> None:
    async with service() as client:
        missing = await client.post("/v1/chat/completions", json={"model": "gpt-dead"})
        get_decide = await client.get(DECIDE)
        get_portkey = await client.get(PORTKEY)
        post_health = await client.post("/healthz")

    assert (missing.status_code, missing.json()) == (404, {"error": "not found"})
    for wrong in (get_decide, get_portkey, post_health):
        assert (wrong.status_code, wrong.json()) == (405, {"error": "method not allowed"})
    assert (get_decide.headers["allow"], post_health.headers["allow"]) == ("POST", "GET")


async def test_lifespan_startup_and_shutdown_complete_so_uvicorn_starts_cleanly() -> None:
    messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

    async def receive() -> dict:
        return next(messages)

    sent = await call(
        DecisionService(make_guard()), {"type": "lifespan", "asgi": {"version": "3.0"}}, receive
    )

    assert sent == [{"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"}]


class ClosingRecorder(ListRecorder):
    """A recorder whose close() is logged into `events`, next to the lifespan messages sent."""

    def __init__(self, events: list, error: Exception | None = None) -> None:
        super().__init__()
        self.events = events
        self.error = error

    def close(self) -> None:
        self.events.append("close")
        if self.error is not None:
            raise self.error


async def run_lifespan(app: DecisionService, events: list) -> None:
    """Drive lifespan startup then shutdown, logging each message the app sends into `events`."""
    messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

    async def receive() -> dict:
        return next(messages)

    async def send(message: dict) -> None:
        events.append(message["type"])

    await app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)


async def test_lifespan_shutdown_flushes_the_recorder_once_before_completing() -> None:
    # uvicorn re-raises SIGTERM after a graceful stop, so atexit never runs; shutdown must flush.
    events: list = []

    await run_lifespan(DecisionService(make_guard(), ClosingRecorder(events)), events)

    assert events == ["lifespan.startup.complete", "close", "lifespan.shutdown.complete"]


async def test_lifespan_shutdown_completes_when_the_recorder_cannot_close(caplog) -> None:
    events: list = []
    recorder = ClosingRecorder(events, error=RuntimeError("disk full"))

    with caplog.at_level(logging.ERROR, logger="sundial"):
        await run_lifespan(DecisionService(make_guard(), recorder), events)

    assert events == ["lifespan.startup.complete", "close", "lifespan.shutdown.complete"]
    assert "could not flush usage counts" in caplog.text


async def test_lifespan_shutdown_completes_for_a_recorder_without_close() -> None:
    events: list = []

    await run_lifespan(DecisionService(make_guard(), ListRecorder()), events)

    assert events == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
