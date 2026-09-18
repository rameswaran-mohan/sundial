import json
from datetime import date, datetime, timezone

import httpx
import pytest

from sundial_llm.guard import IdentityResolver, LifecycleGuard
from sundial_llm.integrations.asgi import SundialMiddleware
from sundial_llm.models import LifecycleRecord, LifecycleState
from sundial_llm.policy import load_policy

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)

POLICY = load_policy(
    {
        "rules": [
            {"name": "block-retired", "when": {"state": "retired"}, "action": "block", "mode": "apply"},
            {"name": "redirect-week", "when": {"days_until_retirement_lte": 7}, "action": "redirect", "mode": "apply"},
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
        payload = json.dumps({"body": body.decode(), "declared_length": declared, "actual_length": len(body)}).encode()
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": payload})


@pytest.fixture
def app() -> EchoApp:
    return EchoApp()


@pytest.fixture
def client(app: EchoApp):
    guard = LifecycleGuard(lambda: RECORDS, IdentityResolver(), POLICY, clock=lambda: NOW)
    transport = httpx.ASGITransport(app=SundialMiddleware(app, guard))
    return httpx.AsyncClient(transport=transport, base_url="http://gateway.test")


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
    assert "gpt-dead" in error["message"] and "2026-01-01" in error["message"] and "gpt-next" in error["message"]
    assert response.headers["x-sundial-state"] == "retired"


async def test_redirect_rewrites_the_model_and_keeps_content_length_honest(client, app) -> None:
    response = await client.post("/v1/chat/completions", json={"model": "gpt-leaving", "messages": [{"role": "user", "content": "héllo"}]})

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


@pytest.mark.parametrize("path", ["/v1/embeddings", "/v1/responses", "/v1/messages", "/openai/deployments/x/chat/completions"])
async def test_every_inference_route_is_guarded(client, path: str) -> None:
    response = await client.post(path, json={"model": "gpt-dead"})

    assert response.status_code == 410


async def test_requests_that_are_not_inference_calls_are_not_inspected(client, app) -> None:
    listed = await client.get("/v1/models")
    other = await client.post("/v1/files", json={"model": "gpt-dead"})

    assert (listed.status_code, other.status_code) == (200, 200)
    assert app.calls == 2


async def test_body_that_is_not_json_is_forwarded_byte_for_byte(client) -> None:
    response = await client.post("/v1/chat/completions", content=b"not json at all", headers={"content-type": "text/plain"})

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
