"""Map a gateway's model string onto a lifecycle record."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from sundial_llm.models import LifecycleRecord, LifecycleState, RecordKey

# provider prefix used by gateways -> (sundial platform, match is approximate)
_PLATFORMS: dict[str, tuple[str, bool]] = {
    "openai": ("openai", False),
    "text-completion-openai": ("openai", False),
    "anthropic": ("anthropic", False),
    "azure": ("azure", False),
    "azure_ai": ("azure", False),
    "bedrock": ("bedrock", False),
    "bedrock_converse": ("bedrock", False),
    "gemini": ("gemini", False),
    # Vertex has no single lifecycle page. Gemini ids are shared with the Gemini API page.
    "vertex_ai": ("gemini", True),
    "vertex_ai_beta": ("gemini", True),
}
_BARE_PREFIXES = (("claude", "anthropic"), ("gemini", "gemini"))
_BEDROCK_ROUTES = ("converse/", "invoke/")
_BEDROCK_REGION = re.compile(r"^(?:us-gov|us|eu|apac|global|jp|au)\.")
_VERSION_SUFFIX = re.compile(r"^(?P<model>.+)-(?P<version>\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class UpstreamModel:
    """The provider-side model behind a gateway deployment."""

    provider: str
    model: str
    base_model: str | None = None


@dataclass(frozen=True)
class Match:
    record: LifecycleRecord
    approximate: bool


def _strip_bedrock(model: str) -> str:
    for route in _BEDROCK_ROUTES:
        if model.startswith(route):
            model = model[len(route) :]
    return _BEDROCK_REGION.sub("", model)


def parse_upstream(model: str, base_model: str | None = None) -> UpstreamModel:
    provider, separator, name = model.partition("/")
    if not separator:
        name = model
        provider = next((p for prefix, p in _BARE_PREFIXES if model.startswith(prefix)), "openai")
    if provider.startswith("bedrock"):
        name = _strip_bedrock(name)
    base = base_model.split("/", 1)[-1] if base_model else None
    return UpstreamModel(provider, name, base)


def _azure(records: Mapping[RecordKey, LifecycleRecord], name: str, today: date) -> Match | None:
    versioned = _VERSION_SUFFIX.match(name)
    if versioned:
        exact = records.get(("azure", versioned.group("model"), versioned.group("version")))
        if exact is not None:
            return Match(exact, approximate=exact.ambiguous)
    versions = [r for key, r in records.items() if key[0] == "azure" and key[1] == name]
    if not versions:
        return None
    if len(versions) == 1:
        return Match(versions[0], approximate=versions[0].ambiguous)
    live = [r for r in versions if r.effective_state(today) is not LifecycleState.RETIRED]
    pool = live or versions
    chosen = min(pool, key=lambda r: (r.retire_on is None, r.retire_on or date.max))
    return Match(chosen, approximate=True)


def find_record(
    records: Mapping[RecordKey, LifecycleRecord], upstream: UpstreamModel, today: date
) -> Match | None:
    platform = _PLATFORMS.get(upstream.provider)
    if platform is None:
        return None
    name, approximate = platform
    if name == "azure":
        return _azure(records, upstream.base_model or upstream.model, today)
    record = records.get((name, upstream.base_model or upstream.model, ""))
    return Match(record, approximate or record.ambiguous) if record is not None else None
