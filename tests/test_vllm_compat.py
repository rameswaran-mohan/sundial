"""vLLM loads `--middleware sundial_llm.integrations.asgi.SundialMiddleware` the way `load_like_vllm`
does, after its own CORS and API-key middleware. These tests run that load on a FastAPI app."""

import importlib
import inspect
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from sundial_llm.integrations.asgi import SundialMiddleware  # noqa: E402
from sundial_llm.models import LifecycleRecord, LifecycleState  # noqa: E402
from sundial_llm.store import Store  # noqa: E402

pytestmark = pytest.mark.anyio

MIDDLEWARE = "sundial_llm.integrations.asgi.SundialMiddleware"
API_KEY = "test-key"
AUTHORIZED = {"authorization": f"Bearer {API_KEY}"}

# The runtime Sundial builds reads the real clock, so the records are dated from today.
TODAY = datetime.now(timezone.utc).date()
RETIRED_ON = TODAY - timedelta(days=30)
RETIRING_ON = TODAY + timedelta(days=30)


def load_like_vllm(app: FastAPI, middleware: str) -> object:
    """vLLM's loader for one --middleware entry (vllm/entrypoints/serve/middleware/register.py)."""
    module_path, object_name = middleware.rsplit(".", 1)
    imported = getattr(importlib.import_module(module_path), object_name)
    if inspect.isclass(imported):
        app.add_middleware(imported)
    elif inspect.iscoroutinefunction(imported):
        app.middleware("http")(imported)
    else:
        raise ValueError(f"Invalid middleware {middleware}. Must be a function or a class.")
    return imported


class APIKeyMiddleware:
    """Stands in for vLLM's AuthenticationMiddleware: /v1 requests need the bearer key."""

    def __init__(self, app, api_key: str) -> None:
        self.app = app
        self.api_key = api_key

    def _refused(self, scope) -> bool:
        if scope["type"] != "http" or scope["method"] == "OPTIONS" or not scope["path"].startswith("/v1"):
            return False
        return dict(scope["headers"]).get(b"authorization") != f"Bearer {self.api_key}".encode()

    async def __call__(self, scope, receive, send) -> None:
        if self._refused(scope):
            await JSONResponse({"error": "Unauthorized"}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)


class VllmLikeServer:
    """A FastAPI app with vLLM's middleware order, Sundial loaded last, and a route that echoes."""

    def __init__(self) -> None:
        self.calls = 0
        self.app = FastAPI()
        self.app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
        self.app.add_middleware(APIKeyMiddleware, api_key=API_KEY)
        self.app.post("/v1/chat/completions")(self._chat)
        self.loaded = load_like_vllm(self.app, MIDDLEWARE)

    async def _chat(self, request: Request) -> dict:
        self.calls += 1
        return {"model": (await request.json()).get("model")}

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://vllm.test")


def record(model_id: str, state: LifecycleState, retire_on) -> LifecycleRecord:
    return LifecycleRecord(
        model_id=model_id,
        platform="openai",
        state=state,
        retire_on=retire_on,
        source_url="https://example.test/deprecations",
        evidence="e",
    )


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> VllmLikeServer:
    db = tmp_path / "vllm.db"
    config = tmp_path / "sundial.yaml"
    config.write_text(
        f"db: {db.as_posix()}\n"
        "registry_url: null\n"
        "auto_scan_hours: 0\n"
        "usage_tracking: false\n"
        "rules:\n"
        "  - {name: block-retired, when: {state: retired}, action: block, mode: apply}\n"
        "  - {name: warn-90, when: {days_until_retirement_lte: 90}, action: warn}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SUNDIAL_CONFIG", str(config))
    monkeypatch.delenv("SUNDIAL_DB", raising=False)
    records = (
        record("gpt-dead", LifecycleState.RETIRED, RETIRED_ON),
        record("gpt-leaving", LifecycleState.DEPRECATED, RETIRING_ON),
    )
    Store(db).save_scan({r.key: r for r in records}, [], datetime.now(timezone.utc))
    return VllmLikeServer()


def test_the_middleware_path_resolves_to_a_class_as_vllm_requires(server: VllmLikeServer) -> None:
    assert server.loaded is SundialMiddleware
    assert inspect.isclass(server.loaded)


async def test_a_retired_model_is_refused_before_vllm_checks_the_api_key(server: VllmLikeServer) -> None:
    async with server.client() as client:
        unauthorized = await client.post("/v1/chat/completions", json={"model": "gpt-fine"})
        refused = await client.post("/v1/chat/completions", json={"model": "gpt-dead"})

    assert unauthorized.status_code == 401  # the API key check is on
    assert refused.status_code == 410
    assert refused.json()["error"]["code"] == "model_retired"
    assert refused.headers["x-sundial-state"] == "retired"
    assert server.calls == 0


async def test_a_warned_model_with_the_api_key_reaches_the_route_with_lifecycle_headers(
    server: VllmLikeServer,
) -> None:
    async with server.client() as client:
        response = await client.post(
            "/v1/chat/completions", json={"model": "gpt-leaving"}, headers=AUTHORIZED
        )

    assert response.status_code == 200
    assert response.json() == {"model": "gpt-leaving"}
    assert parsedate_to_datetime(response.headers["sunset"]).date() == RETIRING_ON
    assert response.headers["x-sundial-state"] == "deprecated"
    assert response.headers["x-sundial-rule"] == "warn-90"
    assert server.calls == 1


async def test_a_healthy_model_passes_untouched(server: VllmLikeServer) -> None:
    async with server.client() as client:
        response = await client.post("/v1/chat/completions", json={"model": "gpt-fine"}, headers=AUTHORIZED)

    assert response.status_code == 200
    assert response.json() == {"model": "gpt-fine"}
    assert "sunset" not in response.headers
    assert not any(name.startswith("x-sundial") for name in response.headers)
    assert server.calls == 1
