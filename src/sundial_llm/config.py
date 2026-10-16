"""Configuration: one YAML file, found through SUNDIAL_CONFIG, with safe defaults when absent."""

from __future__ import annotations

import os
from collections.abc import Mapping
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from sundial_llm.models import LifecycleRecord
from sundial_llm.policy import DEFAULT_POLICY, Policy, Rule


class NotifyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slack_webhook: str | None = None
    webhook: str | None = None


class NewModelRule(BaseModel):
    """Which newly launched models are worth a proposal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    platform: str | None = None
    model_glob: str | None = None

    def matches(self, record: LifecycleRecord) -> bool:
        if self.platform is not None and record.platform != self.platform:
            return False
        return self.model_glob is None or fnmatchcase(record.model_id, self.model_glob)


def _default_db() -> Path:
    return Path.home() / ".sundial" / "sundial.db"


class Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    db: Path = _default_db()
    auto_scan_hours: float = 24
    reload_seconds: float = 300
    rules: tuple[Rule, ...] = DEFAULT_POLICY.rules
    successors: dict[str, str] = {}
    notify: NotifyConfig = NotifyConfig()
    new_models: tuple[NewModelRule, ...] = ()
    # The registry published nightly from the official pages; None means always scan them directly.
    registry_url: str | None = (
        "https://raw.githubusercontent.com/rameswaran-mohan/sundial/main/registry/lifecycle.json"
    )
    # Count requests per public model, team and matched record for `sundial status --in-use`.
    usage_tracking: bool = True
    usage_retention_days: int = 90

    @field_validator("db", mode="before")
    @classmethod
    def _expand_home(cls, value: Any) -> Any:
        return Path(value).expanduser() if isinstance(value, (str, Path)) else value

    @property
    def policy(self) -> Policy:
        return Policy(rules=self.rules, successors=self.successors)


def load_config(path: str | Path | None = None, env: Mapping[str, str] = os.environ) -> Config:
    location = path or env.get("SUNDIAL_CONFIG")
    raw: dict[str, Any] = {}
    if location:
        loaded = yaml.safe_load(Path(location).expanduser().read_text(encoding="utf-8"))
        raw = dict(loaded or {})
    if env.get("SUNDIAL_DB"):
        raw["db"] = env["SUNDIAL_DB"]
    try:
        return Config.model_validate(raw)
    except ValidationError as error:
        raise ValueError(f"invalid sundial config{f' at {location}' if location else ''}: {error}") from error
