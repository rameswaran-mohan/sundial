"""An HTTP decision service for gateways that cannot load Python but can call a webhook first.

    app = DecisionService(runtime.guard, runtime.recorder, token=token)   # any ASGI server

Pure ASGI, no framework. Routes:

    GET  /healthz                 {"status": "ok", "records": N}; open even when a token is set
    POST /v1/decide               the generic contract, for any gateway with a pre-request callout
    POST /portkey/before-request  Portkey's webhook guardrail

Every decision is the one the litellm hook would make for the same upstream model. Sundial never
causes a block through its own failure: an internal error answers allow.
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from sundial_llm.headers import REDIRECTED_FROM, block_message, decision_headers
from sundial_llm.integrations.asgi import Decider, Receive, Recorder, Scope, Send
from sundial_llm.matching import parse_upstream
from sundial_llm.policy import Action, Decision
from sundial_llm.usage import MAX_MODEL_NAME_LENGTH

# A client's model is logged as %.200r: quoted, so it cannot forge a line, and cut to 200 characters.
logger = logging.getLogger("sundial")

Payload = dict[str, Any]

_HEALTH = "/healthz"
_GENERIC = "/v1/decide"
_PORTKEY = "/portkey/before-request"
_METHODS = {_HEALTH: "GET", _GENERIC: "POST", _PORTKEY: "POST"}

# Portkey provider slug -> the gateway prefix Sundial matches on. Any other slug is untracked.
PORTKEY_PROVIDERS: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "azure-openai": "azure",
    "azure-ai": "azure_ai",
    "bedrock": "bedrock",
    "google": "gemini",
    "vertex-ai": "vertex_ai",
}
# Portkey's Azure slugs, whose decisions are never enforced (see _portkey_verdict).
_PORTKEY_AZURE_SLUGS = frozenset({"azure-openai", "azure-ai"})

_INVALID = object()  # a body that is not JSON


class DecisionService:
    def __init__(
        self,
        guard: Decider,
        recorder: Recorder | None = None,
        *,
        token: str | None = None,
        max_body_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self._guard = guard
        self._recorder = recorder
        self._token = token.encode("utf-8") if token is not None else None
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope.get("type")
        if kind == "lifespan":
            await _lifespan(receive, send, self._close_recorder)
        elif kind == "http":
            await self._http(scope, receive, send)

    async def _http(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path")
        method = _METHODS.get(path) if isinstance(path, str) else None
        if method is None:
            await _respond(send, 404, {"error": "not found"})
            return
        if scope.get("method") != method:
            await _respond(send, 405, {"error": "method not allowed"}, allow=method)
            return
        if path == _HEALTH:
            await _respond(send, 200, {"status": "ok", "records": self._record_count()})
            return
        if not self._authorized(scope):
            await _respond(send, 401, {"error": "unauthorized"})
            return
        limit = self._max_body_bytes
        body = await _read_body(receive, limit)
        if body is None:
            return  # the client went away before its body arrived; there is no one to answer
        oversized = len(body) > limit
        if path == _GENERIC:
            if oversized:
                status, answer = 413, {"error": f"body is larger than {limit} bytes"}
            else:
                status, answer = self._generic(_parse(body))
        else:
            status = 200
            if oversized:
                answer = _explain(f"The body is larger than {limit} bytes, so it was not checked.")
            else:
                answer = self._portkey(_parse(body))
        try:
            encoded = _encode(answer)
        except Exception:  # noqa: BLE001 - Sundial never blocks through its own failure
            # A body can parse and still not re-encode, for one inside the redirect envelope.
            logger.exception("sundial: could not encode the answer for %s; allowing the request", path)
            status, encoded = 200, _encode(_generic_failed() if path == _GENERIC else _portkey_failed())
        await _send(send, status, encoded)

    def _authorized(self, scope: Scope) -> bool:
        if self._token is None:
            return True
        header = next((v for k, v in scope.get("headers") or () if k.lower() == b"authorization"), None)
        if header is None:
            return False
        scheme, _, credentials = header.strip().partition(b" ")
        return scheme.lower() == b"bearer" and hmac.compare_digest(credentials.strip(), self._token)

    def _record_count(self) -> int:
        count = getattr(self._guard, "record_count", None)
        if not callable(count):
            return 0
        try:
            return int(count())
        except Exception:  # noqa: BLE001 - a health check must answer
            # Probes are frequent, so this stays at debug level, as in the guard's own count.
            logger.debug("sundial: could not count the loaded lifecycle records", exc_info=True)
            return 0

    def _close_recorder(self) -> None:
        """Write the recorder's buffered counts at shutdown. uvicorn stops gracefully on SIGTERM and
        then re-raises it, so the process dies without running atexit; this is the last chance."""
        close = getattr(self._recorder, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception:  # noqa: BLE001 - shutdown must always complete
            logger.exception("sundial: could not flush usage counts at shutdown")

    def _record(self, model: str, team: str | None, decision: Decision) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.record(model, team, decision.key)
        except Exception:  # noqa: BLE001 - never let usage counting change a decision
            logger.exception("sundial: could not record usage for %.200r", model)

    def _generic(self, request: object) -> tuple[int, Payload]:
        """The generic contract: 400 for a malformed request, otherwise 200 with the decision."""
        if request is _INVALID:
            return 400, {"error": "body is not valid JSON"}
        if not isinstance(request, dict):
            return 400, {"error": "body must be a JSON object"}
        model = request.get("model")
        if not isinstance(model, str) or not model:
            return 400, {"error": "model must be a non-empty string"}
        if len(model) > MAX_MODEL_NAME_LENGTH:
            return 400, {"error": "model is too long"}
        for field in ("provider", "base_model", "team"):
            if not isinstance(request.get(field), (str, type(None))):
                return 400, {"error": f"{field} must be a string or null"}
        try:
            provider = request.get("provider")
            upstream = parse_upstream(f"{provider}/{model}" if provider else model, request.get("base_model"))
            decision = self._guard.decide(model, upstreams=(upstream,))
            self._record(model, request.get("team"), decision)
            return 200, _generic_answer(decision, model)
        except Exception:  # noqa: BLE001 - Sundial never blocks through its own failure
            logger.exception("sundial: lifecycle check failed for %.200r; allowing the request", model)
            return 200, _generic_failed()

    def _portkey(self, context: object) -> Payload:
        """Portkey's webhook guardrail. Always a verdict, and only an enforced block is false."""
        try:
            return self._portkey_verdict(context)
        except Exception:  # noqa: BLE001 - Sundial never blocks through its own failure
            logger.exception("sundial: lifecycle check failed for a Portkey request; allowing it")
            return _portkey_failed()

    def _portkey_verdict(self, context: object) -> Payload:
        if context is _INVALID:
            return _explain("The body is not valid JSON, so it was not checked.")
        if not isinstance(context, dict):
            return _explain("The body is not a JSON object, so it was not checked.")
        event = context.get("eventType")
        if event is not None and event != "beforeRequestHook":
            # transformedData is only applied before the request, and the decision was made then.
            return _explain("Sundial checks requests on beforeRequestHook only.")
        request = context.get("request")
        payload = request.get("json") if isinstance(request, dict) else None
        model = payload.get("model") if isinstance(payload, dict) else None
        if not isinstance(model, str) or not model:
            return _explain("The request names no model, so it was not checked.")
        if len(model) > MAX_MODEL_NAME_LENGTH:
            return _explain(
                f"The model name is longer than {MAX_MODEL_NAME_LENGTH} characters, so it was not checked."
            )
        slug = context.get("provider")
        slug = slug if isinstance(slug, str) else None
        prefix = PORTKEY_PROVIDERS.get(slug) if slug is not None else None
        upstreams = (parse_upstream(f"{prefix}/{model}"),) if prefix else ()
        decision = self._guard.decide(model, upstreams=upstreams)
        if slug in _PORTKEY_AZURE_SLUGS and decision.action is not Action.ALLOW:
            # Portkey routes Azure by deployment id, and the body model need not name the deployment,
            # so even a match that looks exact cannot be trusted enough to block or rewrite.
            decision = replace(decision, enforced=False, approximate=True)
        metadata = context.get("metadata")
        team = metadata.get("team") if isinstance(metadata, dict) else None
        self._record(model, team if isinstance(team, str) else None, decision)
        if prefix is None:
            reason = f"provider '{slug}'" if slug is not None else "no provider"
            return _explain(f"Sundial does not track models for {reason} in Portkey.")
        return _portkey_answer(decision, model, payload)


def _details(decision: Decision, model: str) -> Payload:
    """What a gateway needs to explain a decision that is not an allow."""
    record = decision.record
    return {
        "action": decision.action.value,
        "enforced": decision.enforced,
        "state": decision.state.value if decision.state is not None else None,
        "retire_on": record.retire_on.isoformat() if record is not None and record.retire_on else None,
        "days_left": decision.days_left,
        "replacements": list(record.replacements) if record is not None else [],
        "rule": decision.rule,
        "approximate": decision.approximate,
        "message": block_message(decision, model),
    }


def _enforced_block(decision: Decision) -> bool:
    """The generic `block` flag and a false Portkey verdict, so the two routes cannot disagree."""
    return decision.enforced and decision.action is Action.BLOCK


def _redirect_target(decision: Decision) -> str | None:
    return decision.target if decision.enforced and decision.action is Action.REDIRECT else None


def _generic_allow() -> Payload:
    return {
        "action": Action.ALLOW.value,
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


def _generic_failed() -> Payload:
    return {**_generic_allow(), "error": "lifecycle check failed; the request is allowed"}


def _generic_answer(decision: Decision, model: str) -> Payload:
    if decision.action is Action.ALLOW:
        return _generic_allow()
    details = _details(decision, model)
    target = _redirect_target(decision)
    headers = decision_headers(decision)
    if target:
        headers[REDIRECTED_FROM] = model
    return {
        "action": details.pop("action"),
        "enforced": details.pop("enforced"),
        # One boolean a gateway can key on: true only when Sundial enforces a block.
        "block": _enforced_block(decision),
        "redirect_to": target,
        **details,
        "headers": headers,
    }


def _portkey_answer(decision: Decision, model: str, payload: Payload) -> Payload:
    if decision.action is Action.ALLOW:
        return {"verdict": True}
    data = _details(decision, model)
    if _enforced_block(decision):
        return {"verdict": False, "data": data}
    target = _redirect_target(decision)
    if target:
        data.update(redirected_from=model, target=target)
        # Portkey replaces the whole body with this one, so it is a copy with only the model changed.
        rewritten = {**payload, "model": target}
        return {"verdict": True, "transformedData": {"request": {"json": rewritten}}, "data": data}
    return {"verdict": True, "data": data}


def _explain(explanation: str) -> Payload:
    return {"verdict": True, "data": {"explanation": explanation}}


def _portkey_failed() -> Payload:
    return _explain("Sundial could not check this request, so it is allowed.")


def _parse(body: bytes) -> object:
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        return _INVALID


async def _read_body(receive: Receive, limit: int) -> bytes | None:
    """The body, read chunk by chunk until it ends or passes `limit` bytes; past the limit, the rest
    is never read and what came back is longer than `limit`. None when the client disconnects."""
    body = bytearray()
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            return None
        body += message.get("body", b"")
        if len(body) > limit or not message.get("more_body", False):
            return bytes(body)


def _encode(payload: Payload) -> bytes:
    return json.dumps(payload).encode("utf-8")


async def _respond(send: Send, status: int, payload: Payload, *, allow: str | None = None) -> None:
    """Send one of the service's own small answers, which always encode."""
    await _send(send, status, _encode(payload), allow=allow)


async def _send(send: Send, status: int, body: bytes, *, allow: str | None = None) -> None:
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii"))]
    if allow is not None:
        headers.append((b"allow", allow.encode("ascii")))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _lifespan(receive: Receive, send: Send, on_shutdown: Callable[[], None]) -> None:
    """Nothing to start, but answering lets uvicorn start without a lifespan warning. At shutdown,
    `on_shutdown` runs (and must never raise) before the shutdown is reported complete."""
    while True:
        message = await receive()
        if message.get("type") == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message.get("type") == "lifespan.shutdown":
            on_shutdown()
            await send({"type": "lifespan.shutdown.complete"})
            return
