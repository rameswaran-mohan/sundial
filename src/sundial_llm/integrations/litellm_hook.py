"""litellm proxy plugin.

    litellm_settings:
      callbacks: sundial_llm.integrations.litellm_hook.sundial_hook

Configuration is read from the YAML file named by SUNDIAL_CONFIG. Without one, the default policy
only adds warning headers and never blocks.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from litellm.integrations.custom_logger import CustomLogger

from sundial_llm.headers import REDIRECTED_FROM, block_message, decision_headers
from sundial_llm.matching import UpstreamModel, parse_upstream
from sundial_llm.policy import ALLOW, Action, Decision

logger = logging.getLogger("sundial")

_STASH = "sundial_headers"


class Decider(Protocol):
    def decide(self, public_model: str) -> Decision: ...


class ModelBlocked(Exception):
    """Raised instead of an HTTP error when FastAPI is not installed."""


class _AllowAll:
    def decide(self, public_model: str) -> Decision:
        return ALLOW


def _proxy_router() -> Any:
    # Only meaningful inside a running proxy, where the module is already imported.
    module = sys.modules.get("litellm.proxy.proxy_server")
    return getattr(module, "llm_router", None) if module is not None else None


class LitellmRouterResolver:
    """Resolves a public model group to the provider models of its deployments."""

    def __init__(self, get_router: Callable[[], Any] = _proxy_router) -> None:
        self._get_router = get_router

    def resolve(self, public_model: str) -> Sequence[UpstreamModel]:
        router = self._get_router()
        deployments = router.get_model_list(model_name=public_model) if router is not None else None
        resolved = tuple(
            parse_upstream(params["model"], (deployment.get("model_info") or {}).get("base_model"))
            for deployment in deployments or ()
            if isinstance((params := deployment.get("litellm_params")), dict)
            and isinstance(params.get("model"), str)
        )
        # Wildcard routes and unknown groups: the client sent the provider model string itself.
        return resolved or (parse_upstream(public_model),)


def _refusal(message: str) -> Exception:
    try:
        from fastapi import HTTPException
    except ImportError:
        return ModelBlocked(message)
    return HTTPException(
        status_code=410,
        detail={
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "param": "model",
                "code": "model_retired",
            }
        },
    )


class SundialHook(CustomLogger):
    def __init__(self, guard: Decider | None = None) -> None:
        super().__init__()
        self._guard = guard

    def _decider(self) -> Decider:
        if self._guard is None:
            try:
                from sundial_llm.config import load_config
                from sundial_llm.runtime import build_guard

                self._guard = build_guard(load_config(), LitellmRouterResolver())
            except Exception:  # noqa: BLE001 - a bad config disables Sundial, not the proxy
                logger.exception("sundial: could not start; lifecycle checks are disabled")
                self._guard = _AllowAll()
        return self._guard

    def _evaluate(self, model: str) -> tuple[Decision, dict[str, str]]:
        try:
            decision = self._decider().decide(model)
            return decision, decision_headers(decision)
        except Exception:  # noqa: BLE001 - never let a lifecycle check break a request
            logger.exception("sundial: lifecycle check failed for %s", model)
            return ALLOW, {}

    async def async_pre_call_hook(
        self, user_api_key_dict: Any, cache: Any, data: dict, call_type: Any
    ) -> Any:
        model = data.get("model")
        if not isinstance(model, str) or not model:
            return data
        decision, headers = self._evaluate(model)
        if decision.action is Action.ALLOW:
            return data
        if decision.enforced and decision.action is Action.BLOCK:
            raise _refusal(block_message(decision, model))
        if decision.enforced and decision.action is Action.REDIRECT and decision.target:
            logger.warning("sundial: redirecting %s to %s (rule %s)", model, decision.target, decision.rule)
            data["model"] = decision.target
            headers[REDIRECTED_FROM] = model
        metadata = data.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata[_STASH] = headers
        return data

    async def async_post_call_response_headers_hook(
        self,
        data: dict,
        user_api_key_dict: Any,
        response: Any,
        request_headers: dict[str, str] | None = None,
        litellm_call_info: dict[str, Any] | None = None,
    ) -> dict[str, str] | None:
        metadata = data.get("metadata")
        stashed = metadata.get(_STASH) if isinstance(metadata, dict) else None
        if isinstance(stashed, dict):
            return dict(stashed) or None
        model = data.get("model")
        if not isinstance(model, str) or not model:
            return None
        _, headers = self._evaluate(model)
        return headers or None


sundial_hook = SundialHook()
