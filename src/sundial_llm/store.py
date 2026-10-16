"""SQLite registry of lifecycle records and the events produced by scans."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sundial_llm.events import Event
from sundial_llm.models import LifecycleRecord, RecordKey

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    platform TEXT NOT NULL,
    model_id TEXT NOT NULL,
    version TEXT NOT NULL,
    data TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (platform, model_id, version)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# first_seen is never changed once a key exists. last_seen only moves forward, compared as instants
# (julianday) rather than as text, so timestamps written with different offsets still order correctly.
_UPSERT_RECORD = """
INSERT INTO records (platform, model_id, version, data, first_seen, last_seen)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (platform, model_id, version)
DO UPDATE SET
    data = excluded.data,
    last_seen = CASE
        WHEN julianday(excluded.last_seen) > julianday(records.last_seen) THEN excluded.last_seen
        ELSE records.last_seen
    END
"""

_UPSERT_META = (
    "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value"
)


@dataclass(frozen=True)
class StoredEvent:
    at: datetime
    event: Event


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _meta(self, key: str) -> str | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def load_records(self) -> dict[RecordKey, LifecycleRecord]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT data FROM records").fetchall()
        records = (LifecycleRecord.model_validate_json(row[0]) for row in rows)
        return {record.key: record for record in records}

    def seen(self, key: RecordKey) -> tuple[datetime, datetime] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT first_seen, last_seen FROM records "
                "WHERE platform = ? AND model_id = ? AND version = ?",
                key,
            ).fetchone()
        return (datetime.fromisoformat(row[0]), datetime.fromisoformat(row[1])) if row else None

    def seen_all(self) -> dict[RecordKey, tuple[datetime, datetime]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT platform, model_id, version, first_seen, last_seen FROM records"
            ).fetchall()
        return {
            (platform, model_id, version): (datetime.fromisoformat(first), datetime.fromisoformat(last))
            for platform, model_id, version, first, last in rows
        }

    def last_scan_at(self) -> datetime | None:
        value = self._meta("last_scan_at")
        return datetime.fromisoformat(value) if value else None

    def last_direct_scan_at(self) -> datetime | None:
        """When the provider pages were last scanned directly; registry imports do not count."""
        value = self._meta("last_direct_scan_at")
        return datetime.fromisoformat(value) if value else None

    def baselined_sources(self) -> frozenset[str]:
        value = self._meta("baselined_sources")
        return frozenset(json.loads(value)) if value else frozenset()

    def last_scan_sources(self) -> dict[str, str]:
        """Each source of the last scan mapped to "ok" or the reason it failed."""
        value = self._meta("last_scan_sources")
        return json.loads(value) if value else {}

    def save_scan(
        self,
        records: Mapping[RecordKey, LifecycleRecord],
        events: Sequence[Event],
        at: datetime,
        sources_ok: Iterable[str] = (),
        failures: Mapping[str, str] | None = None,
        seen: Mapping[RecordKey, tuple[datetime, datetime]] | None = None,
        direct: bool = True,
    ) -> None:
        """Persist one scan. `seen` supplies (first_seen, last_seen) for keys whose history is
        already known, such as records imported from a registry document; other keys are stamped `at`.
        `direct` is false for a registry import, which leaves `last_direct_scan_at` alone."""
        stamp = at.isoformat()
        ok = frozenset(sources_ok)
        baselined = sorted(self.baselined_sources() | ok)
        outcomes = {**dict.fromkeys(ok, "ok"), **(failures or {})}
        known = {key: (first.isoformat(), last.isoformat()) for key, (first, last) in (seen or {}).items()}
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                _UPSERT_RECORD,
                [
                    (*key, record.model_dump_json(), *known.get(key, (stamp, stamp)))
                    for key, record in records.items()
                ],
            )
            conn.executemany(
                "INSERT INTO events (at, data) VALUES (?, ?)",
                [(stamp, event.model_dump_json()) for event in events],
            )
            conn.execute(_UPSERT_META, ("last_scan_at", stamp))
            if direct:
                conn.execute(_UPSERT_META, ("last_direct_scan_at", stamp))
            conn.execute(_UPSERT_META, ("baselined_sources", json.dumps(baselined)))
            conn.execute(_UPSERT_META, ("last_scan_sources", json.dumps(outcomes, sort_keys=True)))

    def events(self, limit: int = 50) -> list[StoredEvent]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT at, data FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [StoredEvent(datetime.fromisoformat(at), Event.model_validate_json(data)) for at, data in rows]
