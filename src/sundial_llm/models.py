"""The normalised lifecycle record every source is parsed into."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict

RecordKey = tuple[str, str, str]

_SEVERITY = {"active": 0, "preview": 1, "legacy": 2, "deprecated": 3, "retired": 4}


class LifecycleState(str, Enum):
    PREVIEW = "preview"
    ACTIVE = "active"
    LEGACY = "legacy"
    DEPRECATED = "deprecated"
    RETIRED = "retired"

    @property
    def severity(self) -> int:
        return _SEVERITY[self.value]


class LifecycleRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    platform: str
    model_id: str
    state: LifecycleState
    source_url: str
    evidence: str
    version: str | None = None
    vendor: str | None = None
    released_on: date | None = None
    announced_on: date | None = None
    deprecated_on: date | None = None
    retire_on: date | None = None
    retire_on_is_floor: bool = False
    replacements: tuple[str, ...] = ()
    # The source page contradicts itself about this model, so the dates are a best guess.
    ambiguous: bool = False

    @property
    def key(self) -> RecordKey:
        return (self.platform, self.model_id, self.version or "")

    def days_until_retirement(self, today: date) -> int | None:
        """Days to a firm retirement date. Floors and missing dates give None."""
        if self.retire_on is None or self.retire_on_is_floor:
            return None
        return (self.retire_on - today).days

    def effective_state(self, today: date) -> LifecycleState:
        days = self.days_until_retirement(today)
        if days is not None and days <= 0:
            return LifecycleState.RETIRED
        return self.state


class SourceFormatError(RuntimeError):
    """A source parsed to nothing: the provider changed the page shape."""


@dataclass(frozen=True)
class ParseResult:
    records: tuple[LifecycleRecord, ...]
    warnings: tuple[str, ...] = ()


_FILLABLE = ("version", "vendor", "released_on", "announced_on", "deprecated_on")


def merge_records(a: LifecycleRecord, b: LifecycleRecord) -> LifecycleRecord:
    """Combine two records for the same key: the more severe state wins, gaps are filled."""
    primary, secondary = (b, a) if b.state.severity > a.state.severity else (a, b)
    update: dict[str, object] = {
        name: getattr(secondary, name) for name in _FILLABLE if getattr(primary, name) is None
    }
    if primary.retire_on is None and secondary.retire_on is not None:
        update["retire_on"] = secondary.retire_on
        update["retire_on_is_floor"] = secondary.retire_on_is_floor
    extra = tuple(r for r in secondary.replacements if r not in primary.replacements)
    update["replacements"] = primary.replacements + extra
    return primary.model_copy(update=update)
