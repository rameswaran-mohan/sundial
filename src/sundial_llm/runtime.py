"""Wiring for in-process use: a guard backed by the store, kept fresh by background scans."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sundial_llm.config import Config
from sundial_llm.guard import LifecycleGuard, Records, Resolver
from sundial_llm.notify import Post, http_post, notify_events
from sundial_llm.registry import RegistryDocument, parse_document
from sundial_llm.scan import ScanOutcome, run_import, run_scan
from sundial_llm.sources import ALL_SOURCES, Fetch, http_fetch
from sundial_llm.store import Store

logger = logging.getLogger("sundial")

Spawn = Callable[[Callable[[], None]], None]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _thread_spawn(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="sundial-scan", daemon=True).start()


class AutoScanner:
    """Refreshes the store when it is older than `auto_scan_hours`, from the published registry when
    possible and by scanning the providers otherwise. At most one refresh at a time."""

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
        self.last_mode: str | None = None  # "registry" or "scan": where the last refresh came from

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
            outcome = self._refresh()
            for name, reason in outcome.failures.items():
                logger.warning("sundial: source %s failed: %s", name, reason)
            for error in notify_events(outcome.events, self._config, self._post):
                logger.warning("sundial: %s", error)
        except Exception:  # noqa: BLE001 - a background scan must never surface in the host process
            logger.exception("sundial: background scan failed")
        finally:
            with self._lock:
                self._running = False

    def _refresh(self) -> ScanOutcome:
        """Import the published registry when it is usable, otherwise scan the provider pages."""
        now = self._clock()
        document = self._published_document()
        if document is not None:
            self.last_mode = "registry"
            return run_import(self._store, document, now)
        self.last_mode = "scan"
        return run_scan(self._store, ALL_SOURCES, self._fetch, now)

    def _published_document(self) -> RegistryDocument | None:
        """The registry document if one is configured, readable and newer than the local data."""
        url = self._config.registry_url
        if url is None:
            return None
        last = self._store.last_scan_at()
        try:
            document = parse_document(self._fetch(url))
            newer = last is None or document.generated_at > last
        except Exception as error:  # noqa: BLE001 - any registry problem means scanning the sources instead
            logger.warning("sundial: registry %s unavailable (%s); scanning sources directly", url, error)
            return None
        if not newer:
            # Importing it could roll back a change a direct scan already saw, and events would flap.
            logger.warning(
                "sundial: registry %s is not newer than the local data (generated %s, last refresh %s); "
                "scanning sources directly",
                url,
                document.generated_at,
                last,
            )
            return None
        return document


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
