import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from sundial_llm.guard import IdentityResolver, LifecycleGuard
from sundial_llm.integrations.asgi import SundialMiddleware
from sundial_llm.models import LifecycleRecord, LifecycleState
from sundial_llm.policy import load_policy
from sundial_llm.store import Store
from sundial_llm.usage import UsageRecorder, UsageRow

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
        "successors": {"gpt-leaving": "gpt-next"},
    }
)


def rec(model_id: str, retire_on: date, **extra: object) -> LifecycleRecord:
    base = {"platform": "openai", "state": LifecycleState.DEPRECATED, "source_url": "u", "evidence": "e"}
    return LifecycleRecord(model_id=model_id, retire_on=retire_on, **{**base, **extra})


RECORDS = {
    r.key: r
    for r in (
        rec("gpt-dead", date(2026, 1, 1), replacements=("gpt-next",)),
        rec("gpt-leaving", date(2026, 9, 22)),
        rec("gpt-fading", date(2026, 10, 19), deprecated_on=date(2026, 4, 1)),
    )
}


class EchoApp:
    """Reports what reached the wrapped gateway."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope, receive, send) -> None:
        self.calls += 1
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        declared = dict(scope["headers"]).get(b"content-length", b"").decode()
        payload = json.dumps(
            {"body": body.decode(), "declared_length": declared, "actual_length": len(body)}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": payload})


@pytest.fixture
def app() -> EchoApp:
    return EchoApp()


def identity_guard() -> LifecycleGuard:
    return LifecycleGuard(lambda: RECORDS, IdentityResolver(), POLICY, clock=lambda: NOW)


def gateway(app, guard=None, **kwargs) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=SundialMiddleware(app, guard or identity_guard(), **kwargs))
    return httpx.AsyncClient(transport=transport, base_url="http://gateway.test")


@pytest.fixture
def client(app: EchoApp):
    return gateway(app)


@pytest.fixture
def usage(tmp_path) -> Store:
    return Store(tmp_path / "usage.db")


@pytest.fixture
def recorder(usage: Store) -> UsageRecorder:
    return UsageRecorder(usage, clock=lambda: NOW)


async def test_warned_request_passes_through_untouched_with_lifecycle_headers(client, app) -> None:
    response = await client.post("/v1/chat/completions", json={"model": "gpt-fading", "messages": []})

    assert response.status_code == 200
    assert json.loads(response.json()["body"])["model"] == "gpt-fading"
    assert response.headers["sunset"] == "Mon, 19 Oct 2026 00:00:00 GMT"
    assert response.headers["deprecation"] == "@1775001600"
    assert response.headers["x-sundial-rule"] == "warn-90"


async def test_retired_model_is_refused_with_410_and_never_reaches_the_gateway(client, app) -> None:
    response = await client.post("/v1/chat/completions", json={"model": "gpt-dead"})

    assert response.status_code == 410
    assert app.calls == 0
    error = response.json()["error"]
    assert error["code"] == "model_retired"
    assert (
        "gpt-dead" in error["message"] and "2026-01-01" in error["message"] and "gpt-next" in error["message"]
    )
    assert response.headers["x-sundial-state"] == "retired"


async def test_redirect_rewrites_the_model_and_keeps_content_length_honest(client, app) -> None:
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-leaving", "messages": [{"role": "user", "content": "héllo"}]},
    )

    seen = response.json()
    forwarded = json.loads(seen["body"])
    assert forwarded["model"] == "gpt-next"
    assert forwarded["messages"][0]["content"] == "héllo"
    assert seen["declared_length"] == str(seen["actual_length"])
    assert response.headers["x-sundial-successor"] == "gpt-next"
    assert response.headers["x-sundial-redirected-from"] == "gpt-leaving"


async def test_unknown_model_gets_no_headers(client) -> None:
    response = await client.post("/v1/chat/completions", json={"model": "gpt-fine"})

    assert response.status_code == 200
    assert not any(name.startswith("x-sundial") for name in response.headers)


@pytest.mark.parametrize(
    "path", ["/v1/embeddings", "/v1/responses", "/v1/messages", "/openai/deployments/x/chat/completions"]
)
async def test_every_inference_route_is_guarded(client, path: str) -> None:
    response = await client.post(path, json={"model": "gpt-dead"})

    assert response.status_code == 410


async def test_requests_that_are_not_inference_calls_are_not_inspected(client, app) -> None:
    listed = await client.get("/v1/models")
    other = await client.post("/v1/files", json={"model": "gpt-dead"})

    assert (listed.status_code, other.status_code) == (200, 200)
    assert app.calls == 2


async def test_body_that_is_not_json_is_forwarded_byte_for_byte(client) -> None:
    response = await client.post(
        "/v1/chat/completions", content=b"not json at all", headers={"content-type": "text/plain"}
    )

    assert response.status_code == 200
    assert response.json()["body"] == "not json at all"


async def test_a_guard_that_raises_never_breaks_the_request(app) -> None:
    class Exploding:
        def decide(self, public_model: str):
            raise RuntimeError("boom")

    transport = httpx.ASGITransport(app=SundialMiddleware(app, Exploding()))
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        response = await client.post("/v1/chat/completions", json={"model": "gpt-dead"})

    assert response.status_code == 200
    assert json.loads(response.json()["body"])["model"] == "gpt-dead"


async def test_inference_post_records_usage_with_an_empty_team(app, usage, recorder) -> None:
    async with gateway(app, recorder=recorder) as client:
        await client.post("/v1/chat/completions", json={"model": "gpt-fading", "messages": []})
    recorder.flush()

    assert usage.usage_rows(NOW.date()) == [
        UsageRow(NOW.date(), "gpt-fading", "", "openai", "gpt-fading", "", 1)
    ]


async def test_a_model_name_with_a_lone_surrogate_does_not_stop_usage_tracking(app, usage, recorder) -> None:
    async with gateway(app, recorder=recorder) as client:
        await client.post(
            "/v1/chat/completions",
            content=b'{"model": "\\ud800"}',
            headers={"content-type": "application/json"},
        )
        for _ in range(5):
            await client.post("/v1/chat/completions", json={"model": "gpt-fading"})
    recorder.flush()

    assert usage.usage_rows(NOW.date()) == [
        UsageRow(NOW.date(), "gpt-fading", "", "openai", "gpt-fading", "", 5)
    ]


async def test_a_get_records_no_usage(app, usage, recorder) -> None:
    async with gateway(app, recorder=recorder) as client:
        await client.request("GET", "/v1/chat/completions", json={"model": "gpt-fading"})
    recorder.flush()

    assert usage.usage_rows(NOW.date()) == []


async def test_a_redirected_request_is_recorded_under_the_name_the_client_sent(app, usage, recorder) -> None:
    async with gateway(app, recorder=recorder) as client:
        response = await client.post("/v1/chat/completions", json={"model": "gpt-leaving"})
    recorder.flush()

    assert json.loads(response.json()["body"])["model"] == "gpt-next"
    assert [row.public_model for row in usage.usage_rows(NOW.date())] == ["gpt-leaving"]


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


async def test_a_middleware_with_no_arguments_records_usage_through_the_runtime_it_builds(
    app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = offline_config(tmp_path, monkeypatch)
    middleware = SundialMiddleware(app)

    transport = httpx.ASGITransport(app=middleware)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        response = await client.post("/v1/chat/completions", json={"model": "gpt-wired"})
    adopted = middleware._recorder
    assert isinstance(adopted, UsageRecorder)
    adopted.close()

    assert response.status_code == 200
    assert [(row.public_model, row.team, row.calls) for row in Store(db).usage_rows(date.min)] == [
        ("gpt-wired", "", 1)
    ]


async def test_an_explicit_recorder_is_kept_when_the_guard_is_built_lazily(
    app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = offline_config(tmp_path, monkeypatch)
    explicit = ListRecorder()
    middleware = SundialMiddleware(app, recorder=explicit)

    transport = httpx.ASGITransport(app=middleware)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        await client.post("/v1/chat/completions", json={"model": "gpt-wired"})

    assert middleware._recorder is explicit
    assert explicit.calls == [("gpt-wired", None, None)]
    assert Store(db).usage_rows(date.min) == []


async def test_a_post_whose_body_is_not_json_records_nothing(app) -> None:
    recorder = ListRecorder()

    async with gateway(app, recorder=recorder) as client:
        response = await client.post(
            "/v1/chat/completions", content=b"not json at all", headers={"content-type": "text/plain"}
        )

    assert response.status_code == 200
    assert recorder.calls == []


async def test_a_recorder_that_raises_never_breaks_the_request_or_the_decision(app) -> None:
    class Exploding:
        def record(self, public_model, team, key) -> None:
            raise RuntimeError("boom")

    async with gateway(app, recorder=Exploding()) as client:
        refused = await client.post("/v1/chat/completions", json={"model": "gpt-dead"})
        passed = await client.post("/v1/chat/completions", json={"model": "gpt-fine"})

    assert refused.status_code == 410
    assert passed.status_code == 200


async def test_a_recorder_failure_logs_the_client_model_quoted_and_cut_to_200_characters(app, caplog) -> None:
    class Exploding:
        def record(self, public_model, team, key) -> None:
            raise RuntimeError("boom")

    model = "gpt-x\nERROR sundial: forged line " + "m" * 300
    async with gateway(app, recorder=Exploding()) as client:
        await client.post("/v1/chat/completions", json={"model": model})

    assert caplog.messages == [f"sundial: could not record usage for {repr(model)[:200]}"]


class LifespanApp(EchoApp):
    """An echo gateway that also runs the lifespan protocol, logging each lifespan message it gets."""

    def __init__(self, events: list) -> None:
        super().__init__()
        self.events = events

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "lifespan":
            await super().__call__(scope, receive, send)
            return
        while True:
            message = await receive()
            self.events.append(f"app: {message['type']}")
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return


class ClosingRecorder(ListRecorder):
    """A recorder whose close() is logged into `events`, next to the lifespan messages."""

    def __init__(self, events: list, error: Exception | None = None) -> None:
        super().__init__()
        self.events = events
        self.error = error

    def close(self) -> None:
        self.events.append("close")
        if self.error is not None:
            raise self.error


async def serve_lifespan(middleware: SundialMiddleware, events: list) -> None:
    """Play the server's side of startup then shutdown, logging each message that reaches the server."""
    messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

    async def receive() -> dict:
        return next(messages)

    async def send(message: dict) -> None:
        events.append(f"server: {message['type']}")

    await middleware({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send)


UNTOUCHED_LIFESPAN = [
    "app: lifespan.startup",
    "server: lifespan.startup.complete",
    "app: lifespan.shutdown",
    "server: lifespan.shutdown.complete",
]


async def test_server_shutdown_closes_the_recorder_once_before_the_gateway_sees_it() -> None:
    # uvicorn re-raises SIGTERM after a graceful stop, so atexit never runs; shutdown must flush.
    events: list = []
    middleware = SundialMiddleware(LifespanApp(events), identity_guard(), recorder=ClosingRecorder(events))

    await serve_lifespan(middleware, events)

    assert events == [
        "app: lifespan.startup",
        "server: lifespan.startup.complete",
        "close",
        "app: lifespan.shutdown",
        "server: lifespan.shutdown.complete",
    ]


async def test_shutdown_completes_when_the_recorder_cannot_close(caplog) -> None:
    events: list = []
    recorder = ClosingRecorder(events, error=RuntimeError("disk full"))
    middleware = SundialMiddleware(LifespanApp(events), identity_guard(), recorder=recorder)

    with caplog.at_level(logging.ERROR, logger="sundial"):
        await serve_lifespan(middleware, events)

    assert events == [*UNTOUCHED_LIFESPAN[:2], "close", *UNTOUCHED_LIFESPAN[2:]]
    assert "could not flush usage counts" in caplog.text


async def test_shutdown_with_no_recorder_builds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list = []
    monkeypatch.setattr("sundial_llm.integrations.asgi._default_runtime", lambda: built.append("runtime"))
    events: list = []

    await serve_lifespan(SundialMiddleware(LifespanApp(events)), events)

    assert built == []
    assert events == UNTOUCHED_LIFESPAN


async def test_shutdown_leaves_a_recorder_without_close_alone() -> None:
    events: list = []
    middleware = SundialMiddleware(LifespanApp(events), identity_guard(), recorder=ListRecorder())

    await serve_lifespan(middleware, events)

    assert events == UNTOUCHED_LIFESPAN


async def test_a_middleware_with_no_arguments_writes_its_counts_when_the_server_shuts_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = offline_config(tmp_path, monkeypatch)
    events: list = []
    middleware = SundialMiddleware(LifespanApp(events))

    transport = httpx.ASGITransport(app=middleware)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        await client.post("/v1/chat/completions", json={"model": "gpt-wired"})
    buffered = Store(db).usage_rows(date.min)
    await serve_lifespan(middleware, events)

    assert buffered == []  # the count was only buffered until shutdown
    assert [(row.public_model, row.team, row.calls) for row in Store(db).usage_rows(date.min)] == [
        ("gpt-wired", "", 1)
    ]
    assert events == UNTOUCHED_LIFESPAN
