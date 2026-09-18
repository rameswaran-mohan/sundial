"""Render events as text, turn new models into proposals, and post both to webhooks."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

import httpx

from sundial_llm.config import Config, NewModelRule
from sundial_llm.events import Event, EventType
from sundial_llm.models import LifecycleRecord

Post = Callable[[str, dict[str, Any]], None]

# sundial platform -> provider prefix litellm expects in `litellm_params.model`
_LITELLM_PREFIX = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "bedrock": "bedrock",
    "azure": "azure",
}


def _name(record: LifecycleRecord) -> str:
    version = f" ({record.version})" if record.version else ""
    return f"[{record.platform}] {record.model_id}{version}"


def _retirement(record: LifecycleRecord) -> str:
    if record.retire_on is None:
        return "No retirement date published."
    qualifier = "not sooner than " if record.retire_on_is_floor else ""
    return f"Retires {qualifier}{record.retire_on.isoformat()}."


def _replacement(record: LifecycleRecord) -> str:
    return f" Replacement: {', '.join(record.replacements)}." if record.replacements else ""


def render_event(event: Event) -> str:
    after, before = event.after, event.before
    name = _name(after)
    if event.type is EventType.MODEL_ADDED:
        body = f"{name} is a new {after.state.value} model."
    elif event.type is EventType.DEPRECATION_ANNOUNCED:
        body = f"{name} is now {after.state.value}. {_retirement(after)}{_replacement(after)}"
    elif event.type is EventType.RETIREMENT_DATE_CHANGED:
        was = before.retire_on.isoformat() if before and before.retire_on else "no date"
        now = after.retire_on.isoformat() if after.retire_on else "no date"
        body = f"{name} retirement moved from {was} to {now}.{_replacement(after)}"
    elif event.type is EventType.REPLACEMENT_NAMED:
        body = f"{name} now has a named replacement.{_replacement(after)} {_retirement(after)}"
    elif event.type is EventType.MODEL_RETIRED:
        body = f"{name} is retired. Requests to it will fail.{_replacement(after)}"
    else:
        was = before.state.value if before else "unknown"
        body = f"{name} moved from {was} to {after.state.value}."
    return f"{body} Source: {after.source_url}"


def _litellm_snippet(record: LifecycleRecord) -> str:
    prefix = _LITELLM_PREFIX.get(record.platform, record.platform)
    if record.platform == "azure":
        return (
            f"- model_name: {record.model_id}\n"
            f"  litellm_params:\n"
            f"    model: azure/<your-deployment-name>\n"
            f"  model_info:\n"
            f"    base_model: azure/{record.model_id}"
        )
    return f"- model_name: {record.model_id}\n  litellm_params:\n    model: {prefix}/{record.model_id}"


def render_proposal(event: Event, rules: Sequence[NewModelRule]) -> str | None:
    if event.type is not EventType.MODEL_ADDED:
        return None
    rule = next((r for r in rules if r.matches(event.after)), None)
    if rule is None:
        return None
    return (
        f"Proposal (rule '{rule.name}'): add {event.after.model_id} to your gateway.\n"
        f"litellm model_list entry:\n{_litellm_snippet(event.after)}"
    )


def http_post(url: str, body: dict[str, Any]) -> None:
    httpx.post(url, json=body, timeout=15.0).raise_for_status()


def notify_events(events: Iterable[Event], config: Config, post: Post = http_post) -> list[str]:
    """Post every event to every configured target. Returns errors instead of raising."""
    errors: list[str] = []
    targets = [
        (config.notify.slack_webhook, lambda text: {"text": text}),
        (config.notify.webhook, lambda text: {"text": text, "source": "sundial"}),
    ]
    for event in events:
        proposal = render_proposal(event, config.new_models)
        text = render_event(event) + (f"\n\n{proposal}" if proposal else "")
        for url, shape in targets:
            if not url:
                continue
            try:
                post(url, shape(text))
            except Exception as error:  # noqa: BLE001 - a dead webhook must not stop a scan
                errors.append(f"notify {url}: {type(error).__name__}: {error}")
    return errors
