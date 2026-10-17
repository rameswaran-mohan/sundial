"""Per-request usage counts, buffered in memory and flushed to the store in batches.

Recording sits in the request path, so it only increments a counter under a lock. Once enough calls or
enough time have accumulated, the buffered counts are handed to a background thread that writes them
to SQLite, one flush at a time. Nothing here ever raises.
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

# How long close() waits for a background flush still writing before it flushes the rest itself.
_CLOSE_WAIT_SECONDS = 5.0

_Bucket = tuple[date, str, str, str, str, str]  # day, public_model, team, platform, model_id, version
Spawn = Callable[[Callable[[], None]], None]


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


def _daemon_spawn(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="sundial-usage", daemon=True).start()


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
        spawn: Spawn = _daemon_spawn,
    ) -> None:
        self._store = store
        self._clock = clock
        self._flush_calls = flush_calls
        self._flush_seconds = flush_seconds
        self._retention_days = retention_days
        self._max_pending = max_pending
        self._spawn = spawn
        self._lock = threading.Lock()
        self._pending: dict[_Bucket, int] = {}
        self._buffered = 0  # calls since the last flush attempt
        self._last_flush = clock()
        self._warned_full = False  # one warning per flush cycle while the buffer is full
        self._idle = threading.Event()  # cleared while a background flush is in flight
        self._idle.set()
        self._pruned_day: date | None = None  # old rows are pruned once per UTC day

    def record(self, public_model: str, team: str | None, key: RecordKey | None) -> None:
        """Count one request. `key` is the matched lifecycle record, None when nothing matched.
        Names that are not printable or longer than MAX_MODEL_NAME_LENGTH, and teams that are not
        printable, are ignored. While MAX_PENDING_BUCKETS distinct entries wait to be written, calls that
        would add another are dropped.

        When a flush is due, the buffered counts go to a background flush through `spawn`. Only one is
        in flight at a time; while it runs, new counts stay buffered until the next call that finds a
        flush due."""
        try:
            if not _printable(public_model) or len(public_model) > MAX_MODEL_NAME_LENGTH:
                return
            if team is not None and not _printable(team):
                return
            now = self._clock()
            platform, model_id, version = key or ("", "", "")
            bucket = (now.date(), public_model, team or "", platform, model_id, version)
            warn_full = False
            batch: list[UsageRow] | None = None
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
                if due and self._idle.is_set():
                    batch = self._take(now)
                    self._idle.clear()
            if warn_full:
                logger.warning(
                    "sundial: usage buffer is full (%d entries); new models are not counted until it flushes",
                    self._max_pending,
                )
            if batch is not None:
                self._flush_in_background(batch, now.date())
        except Exception:  # noqa: BLE001 - counting usage must never take a request down
            logger.exception("sundial: could not record usage for %s", public_model)

    def flush(self) -> None:
        """Write the buffered counts on the calling thread, and prune rows older than the retention once
        per UTC day. Counts that fail to write for a reason that may pass, such as a locked database,
        stay buffered for the next flush."""
        try:
            with self._lock:
                now = self._clock()
                batch = self._take(now)
            self._write(batch, now.date())
        except Exception:  # noqa: BLE001 - counting usage must never take a request down
            logger.exception("sundial: usage flush failed; unwritten counts stay buffered")

    def close(self) -> None:
        """Flush on the calling thread, as at interpreter exit, after giving a background flush still
        in flight a few seconds to finish."""
        try:
            self._idle.wait(_CLOSE_WAIT_SECONDS)
        except Exception:  # noqa: BLE001 - closing must never fail
            logger.exception("sundial: could not wait for the usage flush in flight")
        self.flush()

    def _flush_in_background(self, batch: list[UsageRow], today: date) -> None:
        """Hand a batch to `spawn`. If no flush can be started, the counts go back into the buffer."""

        def job() -> None:
            try:
                self._write(batch, today)
            finally:
                with self._lock:
                    self._idle.set()

        try:
            self._spawn(job)
        except Exception:  # noqa: BLE001 - counting usage must never take a request down
            logger.exception("sundial: could not start a usage flush; counts stay buffered")
            self._restore(batch)
            with self._lock:
                self._idle.set()

    def _take(self, now: datetime) -> list[UsageRow]:
        """Empty the buffer into a batch and start a new flush cycle. Call with the lock held."""
        batch = [UsageRow(*bucket, calls) for bucket, calls in self._pending.items()]
        self._pending = {}
        self._last_flush = now
        self._buffered = 0
        self._warned_full = False
        return batch

    def _write(self, batch: Sequence[UsageRow], today: date) -> None:
        """Write a batch, then prune unless that already happened today. Logs and swallows every
        error, since it also runs on the background flush thread."""
        try:
            if batch:
                self._add(batch)
            if self._pruned_day != today:
                self._store.prune_usage(today - timedelta(days=self._retention_days))
                self._pruned_day = today
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
