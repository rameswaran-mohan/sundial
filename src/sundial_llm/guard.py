"""The per-request entry point: public model name in, decision out. It never raises."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sundial_llm.matching import UpstreamModel, find_record, parse_upstream
from sundial_llm.models import LifecycleRecord, RecordKey
from sundial_llm.policy import ALLOW, DEFAULT_POLICY, Action, Decision, Policy

logger = logging.getLogger("sundial")

Records = Mapping[RecordKey, LifecycleRecord]

# After a failed load, the longest wait before the next try. A shorter reload TTL is used instead.
RETRY_SECONDS = 30.0


class Resolver(Protocol):
    """Turns the model name a client sends into the provider models that may serve it."""

    def resolve(self, public_model: str) -> Sequence[UpstreamModel]: ...


class IdentityResolver:
    """For gateways where the client sends the provider model string itself."""

    def resolve(self, public_model: str) -> Sequence[UpstreamModel]:
        return (parse_upstream(public_model),)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _rank(decision: Decision) -> tuple[int, float, bool]:
    """Which deployment's decision speaks for a group: the most severe, then the one retiring soonest,
    then a tracked one, so usage is counted against its record. A rule hit with no retirement date
    ranks as due now; an allowed deployment with no date ranks after every dated one."""
    days: float
    if decision.days_left is not None:
        days = decision.days_left
    else:
        days = math.inf if decision.action is Action.ALLOW else 0
    return (decision.action.severity, -days, decision.key is not None)


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
        self._retry_at: datetime | None = None  # set after a failed load; no load is tried before it

    def _current_records(self, now: datetime) -> Records:
        """The records to decide with, loaded on the first call and again once the reload TTL has
        passed. A failed load is logged and leaves the records already held in use, none at first, and
        no load is tried again for min(reload_seconds, RETRY_SECONDS), so a broken store is not hit by
        every request."""
        if self._retry_at is not None:
            if now < self._retry_at:
                return self._records
        elif self._loaded_at is not None and (now - self._loaded_at).total_seconds() <= self._reload_seconds:
            return self._records
        try:
            records = self._load_records()
        except Exception:  # noqa: BLE001 - keep deciding with what is held
            backoff = min(self._reload_seconds, RETRY_SECONDS)
            self._retry_at = now + timedelta(seconds=backoff)
            logger.warning(
                "sundial: could not load lifecycle records; deciding with the %d already loaded "
                "and trying again in %g seconds",
                len(self._records),
                backoff,
                exc_info=True,
            )
            return self._records
        self._records, self._loaded_at, self._retry_at = records, now, None
        return records

    def record_count(self) -> int:
        """How many records the guard decides with. Loads them first exactly when `decide` would: on
        the first call, once the reload TTL has passed, or once the wait after a failed load is over.
        Never raises; a failed load reports the records already held, 0 if none."""
        try:
            return len(self._current_records(self._clock()))
        except Exception:  # noqa: BLE001 - a health probe must never fail
            logger.debug("sundial: could not load lifecycle records to count them", exc_info=True)
            return len(self._records)

    def decide(self, public_model: str, upstreams: Sequence[UpstreamModel] | None = None) -> Decision:
        """Decide a request for `public_model`. With `upstreams`, the resolver is skipped and exactly
        those provider models are decided, as for a gateway that names the upstream itself. The
        successor for a redirect is always looked up by `public_model`."""
        try:
            return self._decide(public_model, upstreams)
        except Exception:  # noqa: BLE001 - lifecycle checks must never take a request down
            # %.200r: the client's model is quoted, so it cannot forge a log line, and cut short.
            logger.exception("sundial: lifecycle check failed for %.200r; allowing the request", public_model)
            return ALLOW

    def _decide(self, public_model: str, upstreams: Sequence[UpstreamModel] | None) -> Decision:
        now = self._clock()
        today = now.date()
        records = self._current_records(now)
        if upstreams is None:
            upstreams = self._resolver.resolve(public_model)
        decisions = [
            self._policy.decide(find_record(records, upstream, today), today, public_model)
            for upstream in upstreams
        ]
        if not decisions:
            return ALLOW
        worst = max(decisions, key=_rank)
        # A group is only blocked or redirected when every deployment behind it agrees.
        unanimous = all(d.enforced and d.action is worst.action for d in decisions)
        return worst if unanimous else replace(worst, enforced=False)
