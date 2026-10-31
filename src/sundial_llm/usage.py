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
# Names that match no lifecycle record are unbounded too, and each is a row per day in the store. Past
# this many distinct ones in a UTC day, a process counts further ones under OTHER_MODEL, so every
# request is still counted.
MAX_UNTRACKED_NAMES_PER_DAY = 1_000
OTHER_MODEL = "(other)"
# A name with a record is not bounded by the registry either: a caller that names the upstream (the
# decision service's base_model) can put any name on any record. Each distinct name and record pair
# counts once; past this many in a UTC day, further ones are counted as OTHER_MODEL with their record,
# so status --in-use still reports the retirement.
MAX_TRACKED_NAMES_PER_DAY = 5_000
# Teams come from clients too (Portkey fills metadata.team from a request header). A team longer than
# this, or one that is not printable, is counted as no team. Past MAX_TEAMS_PER_DAY distinct teams in a
# UTC day, further ones are counted as OTHER_TEAM.
MAX_TEAM_LENGTH = 256
MAX_TEAMS_PER_DAY = 100
OTHER_TEAM = "(other)"

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


def close_quietly(recorder: object) -> None:
    """Write a recorder's buffered counts at server shutdown, if it has close(). Logs instead of
    raising, so shutdown always completes. uvicorn stops gracefully on SIGTERM and then re-raises it,
    so the process dies without running atexit; lifespan shutdown is the last chance."""
    try:
        close = getattr(recorder, "close", None)
        if callable(close):
            close()
    except Exception:  # noqa: BLE001 - shutdown must always complete
        logger.exception("sundial: could not flush usage counts at shutdown")


def _bucket(row: UsageRow) -> _Bucket:
    return (row.day, row.public_model, row.team, row.platform, row.model_id, row.version)


def _team(team: object) -> str:
    """The team to count a call under: "" for none, and for one too long or not printable."""
    if isinstance(team, str) and team.isprintable() and len(team) <= MAX_TEAM_LENGTH:
        return team
    return ""


class _DailyCap:
    """At most `limit` distinct values per UTC day. Not locked; the recorder calls it under its lock."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._day: date | None = None
        self._seen: set[object] = set()
        self._capped = False  # whether a value was refused on _day

    def admit(self, day: date, value: object) -> tuple[bool, bool]:
        """Whether `value` is kept apart on `day`, and whether it is the first value refused that day."""
        if day != self._day:
            self._day, self._seen, self._capped = day, set(), False
        if value in self._seen:
            return True, False
        if len(self._seen) < self._limit:
            self._seen.add(value)
            return True, False
        first, self._capped = not self._capped, True
        return False, first


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
        max_untracked_names: int = MAX_UNTRACKED_NAMES_PER_DAY,
        max_tracked_names: int = MAX_TRACKED_NAMES_PER_DAY,
        max_teams: int = MAX_TEAMS_PER_DAY,
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
        self._max_untracked_names = max_untracked_names
        self._max_tracked_names = max_tracked_names
        self._max_teams = max_teams
        self._untracked = _DailyCap(max_untracked_names)  # names with no record
        self._tracked = _DailyCap(max_tracked_names)  # (name, record) pairs
        self._teams = _DailyCap(max_teams)

    def record(self, public_model: str, team: str | None, key: RecordKey | None) -> None:
        """Count one request. `key` is the matched lifecycle record, None when nothing matched.
        Names that are not printable or longer than MAX_MODEL_NAME_LENGTH are ignored. A team that is
        not printable or longer than MAX_TEAM_LENGTH is counted as no team. In a UTC day, untracked
        names past `max_untracked_names` distinct ones and name and record pairs past
        `max_tracked_names` are counted as OTHER_MODEL, the latter with their record, and teams past
        `max_teams` are counted as OTHER_TEAM. While MAX_PENDING_BUCKETS distinct entries wait to be
        written, calls that would add another are dropped.

        When a flush is due, the buffered counts go to a background flush through `spawn`. Only one is
        in flight at a time; while it runs, new counts stay buffered until the next call that finds a
        flush due."""
        try:
            if not _printable(public_model) or len(public_model) > MAX_MODEL_NAME_LENGTH:
                return
            team = _team(team)
            now = self._clock()
            day = now.date()
            platform, model_id, version = key or ("", "", "")
            warn_full = False
            capped: list[tuple[str, str]] = []  # (what, counted as) for each cap this call first reached
            batch: list[UsageRow] | None = None
            with self._lock:
                name = self._name(day, public_model, key, capped)
                if team:
                    kept, first = self._teams.admit(day, team)
                    if not kept:
                        team = OTHER_TEAM
                    if first:
                        capped.append((f"{self._max_teams} distinct teams", OTHER_TEAM))
                bucket = (day, name, team, platform, model_id, version)
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
            for what, other in capped:
                logger.warning("sundial: %s seen today; further ones are counted as %s", what, other)
            if batch is not None:
                self._flush_in_background(batch, day)
        except Exception:  # noqa: BLE001 - counting usage must never take a request down
            # %.200r: the client's model is quoted, so it cannot forge a log line, and cut short.
            logger.exception("sundial: could not record usage for %.200r", public_model)

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

    def _name(
        self, day: date, public_model: str, key: RecordKey | None, capped: list[tuple[str, str]]
    ) -> str:
        """The name to count a call under. When this call is the first one today that a cap refuses,
        the cap is added to `capped`. Call with the lock held."""
        if key is None:
            kept, first = self._untracked.admit(day, public_model)
        else:
            kept, first = self._tracked.admit(day, (public_model, *key))  # hashable even for a list
        if first:
            if key is None:
                what = f"{self._max_untracked_names} distinct untracked model names"
            else:
                what = f"{self._max_tracked_names} distinct model names with a lifecycle record"
            capped.append((what, OTHER_MODEL))
        return public_model if kept else OTHER_MODEL

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
