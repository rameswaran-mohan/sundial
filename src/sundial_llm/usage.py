"""Per-request usage counts, buffered in memory and flushed to the store in batches.

Recording sits in the request path, so it only increments a counter under a lock; the SQLite write
happens inline once enough calls or enough time have accumulated. Nothing here ever raises.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sundial_llm.models import RecordKey

if TYPE_CHECKING:
    from sundial_llm.store import Store

logger = logging.getLogger("sundial")

_Bucket = tuple[date, str, str, str, str, str]  # day, public_model, team, platform, model_id, version


@dataclass(frozen=True)
class UsageRow:
    day: date
    public_model: str
    team: str
    platform: str
    model_id: str
    version: str
    calls: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UsageRecorder:
    def __init__(
        self,
        store: Store,
        *,
        clock: Callable[[], datetime] = _utcnow,
        flush_calls: int = 50,
        flush_seconds: float = 30.0,
        retention_days: int = 90,
    ) -> None:
        self._store = store
        self._clock = clock
        self._flush_calls = flush_calls
        self._flush_seconds = flush_seconds
        self._retention_days = retention_days
        self._lock = threading.Lock()
        self._pending: dict[_Bucket, int] = {}
        self._buffered = 0  # calls since the last flush attempt
        self._last_flush = clock()

    def record(self, public_model: str, team: str | None, key: RecordKey | None) -> None:
        """Count one request. `key` is the matched lifecycle record, None when nothing matched."""
        try:
            now = self._clock()
            platform, model_id, version = key or ("", "", "")
            bucket = (now.date(), public_model, team or "", platform, model_id, version)
            with self._lock:
                self._pending[bucket] = self._pending.get(bucket, 0) + 1
                self._buffered += 1
                due = (
                    self._buffered >= self._flush_calls
                    or (now - self._last_flush).total_seconds() >= self._flush_seconds
                )
            if due:
                self.flush()
        except Exception:  # noqa: BLE001 - counting usage must never take a request down
            logger.exception("sundial: could not record usage for %s", public_model)

    def flush(self) -> None:
        """Write the buffered counts and prune rows older than the retention. A failed write keeps the
        counts buffered for the next flush."""
        try:
            with self._lock:
                now = self._clock()
                self._last_flush = now
                self._buffered = 0
                if self._pending:
                    self._store.add_usage(
                        [UsageRow(*bucket, calls) for bucket, calls in self._pending.items()]
                    )
                    self._pending.clear()
                self._store.prune_usage(now.date() - timedelta(days=self._retention_days))
        except Exception:  # noqa: BLE001 - counting usage must never take a request down
            logger.exception("sundial: usage flush failed; unwritten counts stay buffered")

    def close(self) -> None:
        self.flush()
