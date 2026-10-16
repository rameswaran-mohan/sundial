"""ASGI middleware for any OpenAI-compatible gateway. No framework dependency.

    app = SundialMiddleware(app)            # FastAPI, Starlette, or any ASGI app

It inspects POST bodies on inference routes, reads `model`, and then blocks, rewrites or adds
lifecycle headers. Anything it does not understand is forwarded untouched.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import TYPE_CHECKING, Any, Protocol

from sundial_llm.headers import REDIRECTED_FROM, block_message, decision_headers
from sundial_llm.models import RecordKey
from sundial_llm.policy import Action, Decision

if TYPE_CHECKING:
    from sundial_llm.runtime import Runtime

logger = logging.getLogger("sundial")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

INFERENCE_SUFFIXES = ("/chat/completions", "/completions", "/embeddings", "/responses", "/messages")


class Decider(Protocol):
    def decide(self, public_model: str) -> Decision: ...


class Recorder(Protocol):
    def record(self, public_model: str, team: str | None, key: RecordKey | None) -> None: ...


def _default_runtime() -> Runtime:
    from sundial_llm.config import load_config
    from sundial_llm.guard import IdentityResolver
    from sundial_llm.runtime import build_runtime

    return build_runtime(load_config(), IdentityResolver())


class SundialMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        guard: Decider | None = None,
        *,
        recorder: Recorder | None = None,
        max_body_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.app = app
        self._guard = guard
        self._recorder = recorder
        self._max_body_bytes = max_body_bytes

    def _inspectable(self, scope: Scope) -> bool:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return False
        if not str(scope.get("path", "")).rstrip("/").endswith(INFERENCE_SUFFIXES):
            return False
        declared = dict(scope.get("headers") or []).get(b"content-length")
        return declared is not None and declared.isdigit() and int(declared) <= self._max_body_bytes

    def _decide(self, body: bytes) -> tuple[Decision | None, dict[str, Any] | None, str | None]:
        try:
            payload = json.loads(body)
            model = payload.get("model") if isinstance(payload, dict) else None
            if not isinstance(model, str) or not model:
                return None, None, None
            if self._guard is None:
                runtime = _default_runtime()
                self._guard = runtime.guard
                if self._recorder is None:
                    self._recorder = runtime.recorder
            return self._guard.decide(model), payload, model
        except Exception:  # noqa: BLE001 - never let a lifecycle check break a request
            logger.debug("sundial: request not inspected", exc_info=True)
            return None, None, None

    def _record(self, model: str, decision: Decision) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.record(model, None, decision.key)
        except Exception:  # noqa: BLE001 - never let usage counting break a request
            logger.exception("sundial: could not record usage for %s", model)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._inspectable(scope):
            await self.app(scope, receive, send)
            return

        body = b""
        trailing: list[Message] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                trailing.append(message)
                break
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        decision, payload, model = self._decide(body)
        if decision is not None and model is not None:
            # Counted under the name the client sent, before any block or redirect.
            self._record(model, decision)
        extra: dict[str, str] = {}
        if decision is not None and decision.action is not Action.ALLOW and model is not None:
            extra = decision_headers(decision)
            if decision.enforced and decision.action is Action.BLOCK:
                await _refuse(send, block_message(decision, model), extra)
                return
            if (
                decision.enforced
                and decision.action is Action.REDIRECT
                and decision.target
                and payload is not None
            ):
                payload["model"] = decision.target
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                scope = _with_content_length(scope, len(body))
                extra[REDIRECTED_FROM] = model

        queue: list[Message] = [{"type": "http.request", "body": body, "more_body": False}, *trailing]

        async def replay() -> Message:
            return queue.pop(0) if queue else await receive()

        async def send_with_headers(message: Message) -> None:
            if extra and message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [*message.get("headers", []), *_encode(extra)]
            await send(message)

        await self.app(scope, replay, send_with_headers)


def _encode(headers: dict[str, str]) -> list[tuple[bytes, bytes]]:
    return [
        (name.lower().encode("latin-1"), value.encode("latin-1", "replace"))
        for name, value in headers.items()
    ]


def _with_content_length(scope: Scope, length: int) -> Scope:
    headers = [(k, v) for k, v in scope.get("headers") or [] if k != b"content-length"]
    headers.append((b"content-length", str(length).encode("ascii")))
    return {**scope, "headers": headers}


async def _refuse(send: Send, message: str, extra: dict[str, str]) -> None:
    body = json.dumps(
        {
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "param": "model",
                "code": "model_retired",
            }
        }
    ).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        *_encode(extra),
    ]
    await send({"type": "http.response.start", "status": 410, "headers": headers})
    await send({"type": "http.response.body", "body": body})
