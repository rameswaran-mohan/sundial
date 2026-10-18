"""First-match rules that turn a matched lifecycle record into a per-request decision."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import Enum
from fnmatch import fnmatchcase
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from sundial_llm.matching import Match
from sundial_llm.models import LifecycleRecord, LifecycleState, RecordKey


class Action(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    REDIRECT = "redirect"
    BLOCK = "block"

    @property
    def severity(self) -> int:
        return ("allow", "warn", "redirect", "block").index(self.value)


class Mode(str, Enum):
    PROPOSE = "propose"
    APPLY = "apply"


class When(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: LifecycleState | None = None
    days_until_retirement_lte: int | None = None
    platform: str | None = None
    model_glob: str | None = None
    has_replacement: bool | None = None

    def matches(self, record: LifecycleRecord, today: date) -> bool:
        if self.state is not None and record.effective_state(today) is not self.state:
            return False
        if self.days_until_retirement_lte is not None:
            days = record.days_until_retirement(today)
            if days is None or days > self.days_until_retirement_lte:
                return False
        if self.platform is not None and record.platform != self.platform:
            return False
        if self.model_glob is not None and not fnmatchcase(record.model_id, self.model_glob):
            return False
        return self.has_replacement is None or bool(record.replacements) is self.has_replacement


class Rule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    when: When = When()
    action: Action
    mode: Mode = Mode.PROPOSE


@dataclass(frozen=True)
class Decision:
    action: Action = Action.ALLOW
    mode: Mode = Mode.PROPOSE
    enforced: bool = False
    rule: str | None = None
    record: LifecycleRecord | None = None
    state: LifecycleState | None = None
    days_left: int | None = None
    target: str | None = None
    approximate: bool = False
    # The matched lifecycle record, also on allowed decisions, so usage can be counted per record.
    # Allowed decisions for a matched record also carry days_left.
    key: RecordKey | None = None


ALLOW = Decision()


class Policy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    rules: tuple[Rule, ...] = ()
    successors: dict[str, str] = {}

    def decide(self, match: Match | None, today: date, public_model: str) -> Decision:
        if match is None:
            return ALLOW
        record = match.record
        rule = next((r for r in self.rules if r.when.matches(record, today)), None)
        if rule is None or rule.action is Action.ALLOW:
            # No headers on an allowed request, but days_left lets a group prefer the deployment
            # retiring soonest when several are healthy.
            return Decision(key=record.key, days_left=record.days_until_retirement(today))
        action = rule.action
        target = self.successors.get(public_model) if action is Action.REDIRECT else None
        if action is Action.REDIRECT and target is None:
            action = Action.WARN
        enforceable = action in (Action.BLOCK, Action.REDIRECT) and not match.approximate
        return Decision(
            action=action,
            mode=rule.mode,
            enforced=enforceable and rule.mode is Mode.APPLY,
            rule=rule.name,
            record=record,
            state=record.effective_state(today),
            days_left=record.days_until_retirement(today),
            target=target,
            approximate=match.approximate,
            key=record.key,
        )


def load_policy(config: Mapping[str, Any]) -> Policy:
    try:
        return Policy.model_validate(dict(config))
    except ValidationError as error:
        raise ValueError(f"invalid sundial policy: {error}") from error


DEFAULT_POLICY = load_policy(
    {
        "rules": [
            {"name": "retired", "when": {"state": "retired"}, "action": "warn"},
            {"name": "retiring-within-90-days", "when": {"days_until_retirement_lte": 90}, "action": "warn"},
        ]
    }
)
