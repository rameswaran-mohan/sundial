"""Wiring for in-process use: a guard backed by the store, kept fresh by background scans."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sundial_llm.config import Config
from sundial_llm.guard import LifecycleGuard, Records, Resolver
from sundial_llm.notify import Post, http_post, notify_events
from sundial_llm.scan import run_scan
from sundial_llm.sources import ALL_SOURCES, Fetch, http_fetch
from sundial_llm.store import Store

logger = logging.getLogger("sundial")

Spawn = Callable[[Callable[[], None]], None]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _thread_spawn(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="sundial-scan", daemon=True).start()


class AutoScanner:
    """Starts a scan when the registry is older than `auto_scan_hours`. At most one scan at a time."""

    def __init__(
        self,
        store: Store,
        config: Config,
        fetch: Fetch = http_fetch,
        *,
        clock: Callable[[], datetime] = _utcnow,
        spawn: Spawn = _thread_spawn,
        post: Post = http_post,
    ) -> None:
        self._store = store
        self._config = config
        self._fetch = fetch
        self._clock = clock
        self._spawn = spawn
        self._post = post
        self._lock = threading.Lock()
        self._running = False

    def maybe_start(self) -> bool:
        hours = self._config.auto_scan_hours
        if hours <= 0:
            return False
        last = self._store.last_scan_at()
        if last is not None and self._clock() - last < timedelta(hours=hours):
            return False
        with self._lock:
            if self._running:
                return False
            self._running = True
        self._spawn(self._scan)
        return True

    def _scan(self) -> None:
        try:
            outcome = run_scan(self._store, ALL_SOURCES, self._fetch, self._clock())
            for name, reason in outcome.failures.items():
                logger.warning("sundial: source %s failed: %s", name, reason)
            for error in notify_events(outcome.events, self._config, self._post):
                logger.warning("sundial: %s", error)
        except Exception:  # noqa: BLE001 - a background scan must never surface in the host process
            logger.exception("sundial: background scan failed")
        finally:
            with self._lock:
                self._running = False


def build_guard(
    config: Config,
    resolver: Resolver,
    *,
    fetch: Fetch = http_fetch,
    clock: Callable[[], datetime] = _utcnow,
    spawn: Spawn = _thread_spawn,
) -> LifecycleGuard:
    store = Store(config.db)
    scanner = AutoScanner(store, config, fetch, clock=clock, spawn=spawn)

    def load() -> Records:
        scanner.maybe_start()
        return store.load_records()

    return LifecycleGuard(load, resolver, config.policy, clock=clock, reload_seconds=config.reload_seconds)
