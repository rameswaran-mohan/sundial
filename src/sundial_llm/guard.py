"""The per-request entry point: public model name in, decision out. It never raises."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol

from sundial_llm.matching import UpstreamModel, find_record, parse_upstream
from sundial_llm.models import LifecycleRecord, RecordKey
from sundial_llm.policy import ALLOW, DEFAULT_POLICY, Decision, Policy

logger = logging.getLogger("sundial")

Records = Mapping[RecordKey, LifecycleRecord]


class Resolver(Protocol):
    """Turns the model name a client sends into the provider models that may serve it."""

    def resolve(self, public_model: str) -> Sequence[UpstreamModel]: ...


class IdentityResolver:
    """For gateways where the client sends the provider model string itself."""

    def resolve(self, public_model: str) -> Sequence[UpstreamModel]:
        return (parse_upstream(public_model),)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LifecycleGuard:
    def __init__(
        self,
        load_records: Callable[[], Records],
        resolver: Resolver,
        policy: Policy = DEFAULT_POLICY,
        *,
        clock: Callable[[], datetime] = _utcnow,
        reload_seconds: float = 300.0,
    ) -> None:
        self._load_records = load_records
        self._resolver = resolver
        self._policy = policy
        self._clock = clock
        self._reload_seconds = reload_seconds
        self._records: Records = {}
        self._loaded_at: datetime | None = None

    def _current_records(self, now: datetime) -> Records:
        stale = self._loaded_at is None or (now - self._loaded_at).total_seconds() > self._reload_seconds
        if stale:
            self._records = self._load_records()
            self._loaded_at = now
        return self._records

    def decide(self, public_model: str) -> Decision:
        try:
            return self._decide(public_model)
        except Exception:  # noqa: BLE001 - lifecycle checks must never take a request down
            logger.exception("sundial: lifecycle check failed for %s; allowing the request", public_model)
            return ALLOW

    def _decide(self, public_model: str) -> Decision:
        now = self._clock()
        today = now.date()
        records = self._current_records(now)
        decisions = [
            self._policy.decide(find_record(records, upstream, today), today, public_model)
            for upstream in self._resolver.resolve(public_model)
        ]
        if not decisions:
            return ALLOW
        # On a tie, a decision for a tracked deployment wins, so usage is counted against its record.
        worst = max(decisions, key=lambda d: (d.action.severity, -(d.days_left or 0), d.key is not None))
        # A group is only blocked or redirected when every deployment behind it agrees.
        unanimous = all(d.enforced and d.action is worst.action for d in decisions)
        return worst if unanimous else replace(worst, enforced=False)
