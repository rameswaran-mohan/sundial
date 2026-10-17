"""Per-request usage counts, buffered in memory and flushed to the store in batches.

Recording sits in the request path, so it only increments a counter under a lock; the SQLite write
happens inline once enough calls or enough time have accumulated. Nothing here ever raises.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sundial_llm.models import RecordKey

if TYPE_CHECKING:
    from sundial_llm.store import Store

logger = logging.getLogger("sundial")

# Model names come from clients, so both how long one may be and how many are buffered are bounded.
MAX_MODEL_NAME_LENGTH = 256
MAX_PENDING_BUCKETS = 10_000

# A write that fails with one of these may succeed later, so its counts stay buffered. Any other failure
# means some row can never be written: the batch is retried row by row and those rows are dropped.
_TRANSIENT = (sqlite3.OperationalError, OSError)

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


def _printable(value: object) -> bool:
    """Ordinary text, non-ASCII included. Rejects surrogates SQLite cannot encode, control characters
    and escape sequences, so a client cannot poison a flush or a terminal."""
    return isinstance(value, str) and value.isprintable()


def _bucket(row: UsageRow) -> _Bucket:
    return (row.day, row.public_model, row.team, row.platform, row.model_id, row.version)


class UsageRecorder:
    def __init__(
        self,
        store: Store,
        *,
        clock: Callable[[], datetime] = _utcnow,
        flush_calls: int = 50,
        flush_seconds: float = 30.0,
        retention_days: int = 90,
        max_pending: int = MAX_PENDING_BUCKETS,
    ) -> None:
        self._store = store
        self._clock = clock
        self._flush_calls = flush_calls
        self._flush_seconds = flush_seconds
        self._retention_days = retention_days
        self._max_pending = max_pending
        self._lock = threading.Lock()
        self._pending: dict[_Bucket, int] = {}
        self._buffered = 0  # calls since the last flush attempt
        self._last_flush = clock()
        self._warned_full = False  # one warning per flush cycle while the buffer is full

    def record(self, public_model: str, team: str | None, key: RecordKey | None) -> None:
        """Count one request. `key` is the matched lifecycle record, None when nothing matched.
        Names that are not printable or longer than MAX_MODEL_NAME_LENGTH, and teams that are not
        printable, are ignored. While MAX_PENDING_BUCKETS distinct entries wait to be written, calls that
        would add another are dropped."""
        try:
            if not _printable(public_model) or len(public_model) > MAX_MODEL_NAME_LENGTH:
                return
            if team is not None and not _printable(team):
                return
            now = self._clock()
            platform, model_id, version = key or ("", "", "")
            bucket = (now.date(), public_model, team or "", platform, model_id, version)
            warn_full = False
            with self._lock:
                if bucket in self._pending or len(self._pending) < self._max_pending:
                    self._pending[bucket] = self._pending.get(bucket, 0) + 1
                    self._buffered += 1
                elif not self._warned_full:
                    self._warned_full = warn_full = True
                due = (
                    self._buffered >= self._flush_calls
                    or (now - self._last_flush).total_seconds() >= self._flush_seconds
                )
            if warn_full:
                logger.warning(
                    "sundial: usage buffer is full (%d entries); new models are not counted until it flushes",
                    self._max_pending,
                )
            if due:
                self.flush()
        except Exception:  # noqa: BLE001 - counting usage must never take a request down
            logger.exception("sundial: could not record usage for %s", public_model)

    def flush(self) -> None:
        """Write the buffered counts and prune rows older than the retention. Counts that fail to write
        for a reason that may pass, such as a locked database, stay buffered for the next flush."""
        try:
            with self._lock:
                now = self._clock()
                batch = self._take(now)
            self._write(batch, now.date())
        except Exception:  # noqa: BLE001 - counting usage must never take a request down
            logger.exception("sundial: usage flush failed; unwritten counts stay buffered")

    def close(self) -> None:
        self.flush()

    def _take(self, now: datetime) -> list[UsageRow]:
        """Empty the buffer into a batch and start a new flush cycle. Call with the lock held."""
        batch = [UsageRow(*bucket, calls) for bucket, calls in self._pending.items()]
        self._pending = {}
        self._last_flush = now
        self._buffered = 0
        self._warned_full = False
        return batch

    def _write(self, batch: Sequence[UsageRow], today: date) -> None:
        """Write a batch and prune. Logs and swallows every error."""
        try:
            if batch:
                self._add(batch)
            self._store.prune_usage(today - timedelta(days=self._retention_days))
        except Exception:  # noqa: BLE001 - counting usage must never take a request down
            logger.exception("sundial: usage flush failed; unwritten counts stay buffered")

    def _add(self, batch: Sequence[UsageRow]) -> None:
        """Add a batch to the store. A transient failure puts the unwritten rows back in the buffer and
        is raised. Any other failure is retried row by row, and the rows that still fail are dropped."""
        try:
            self._store.add_usage(batch)
            return
        except _TRANSIENT:
            self._restore(batch)
            raise
        except Exception:  # noqa: BLE001 - some row can never be written; find it below
            logger.debug("sundial: usage batch rejected; writing it row by row", exc_info=True)
        dropped = 0
        try:
            for index, row in enumerate(batch):
                try:
                    self._store.add_usage([row])
                except _TRANSIENT:
                    self._restore(batch[index:])
                    raise
                except Exception:  # noqa: BLE001 - this row can never be written
                    dropped += 1
        finally:
            if dropped:
                logger.warning(
                    "sundial: dropped %d usage %s that could not be written",
                    dropped,
                    "row" if dropped == 1 else "rows",
                )

    def _restore(self, rows: Sequence[UsageRow]) -> None:
        """Return unwritten rows to the buffer, ahead of entries buffered since they were taken."""
        with self._lock:
            merged = {_bucket(row): row.calls for row in rows}
            for bucket, calls in self._pending.items():
                if bucket in merged:
                    merged[bucket] += calls
                elif len(merged) < self._max_pending:
                    merged[bucket] = calls
            self._pending = merged
