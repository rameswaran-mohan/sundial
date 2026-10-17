import logging
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

import pytest

from sundial_llm.store import Store
from sundial_llm.usage import MAX_MODEL_NAME_LENGTH, UsageRecorder, UsageRow

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
GPT4 = ("openai", "gpt-4-0613", "")


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def inline(job) -> None:
    job()


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "usage.db")


def row(day: date, calls: int, team: str = "") -> UsageRow:
    return UsageRow(day, "gpt-4", team, *GPT4, calls)


def test_adding_usage_for_the_same_key_twice_sums_the_calls(store: Store) -> None:
    store.add_usage([row(TODAY, 2)])
    store.add_usage([row(TODAY, 3)])

    assert store.usage_rows(TODAY) == [row(TODAY, 5)]


def test_usage_rows_leave_out_days_before_the_window(store: Store) -> None:
    store.add_usage([row(TODAY - timedelta(days=10), 1), row(TODAY - timedelta(days=2), 4)])

    assert store.usage_rows(TODAY - timedelta(days=5)) == [row(TODAY - timedelta(days=2), 4)]


def test_prune_usage_deletes_older_days_and_returns_how_many_rows_went(store: Store) -> None:
    old = TODAY - timedelta(days=100)
    store.add_usage([row(old, 1), row(old, 1, team="payments"), row(TODAY, 1)])

    assert store.prune_usage(TODAY - timedelta(days=90)) == 2
    assert store.usage_rows(date.min) == [row(TODAY, 1)]
    assert store.prune_usage(TODAY - timedelta(days=90)) == 0


def test_reaching_flush_calls_writes_one_row_holding_every_call(store: Store) -> None:
    recorder = UsageRecorder(store, clock=Clock(), flush_calls=5, spawn=inline)

    for _ in range(4):
        recorder.record("gpt-4", "payments", GPT4)
    assert store.usage_rows(TODAY) == []
    recorder.record("gpt-4", "payments", GPT4)

    assert store.usage_rows(TODAY) == [row(TODAY, 5, team="payments")]


def test_a_call_after_flush_seconds_flushes_everything_buffered(store: Store) -> None:
    clock = Clock()
    recorder = UsageRecorder(store, clock=clock, flush_seconds=30, spawn=inline)

    recorder.record("gpt-4", None, GPT4)
    assert store.usage_rows(TODAY) == []
    clock.now = NOW + timedelta(seconds=31)
    recorder.record("gpt-4", None, GPT4)

    assert store.usage_rows(TODAY) == [row(TODAY, 2)]


def test_untracked_models_and_unknown_teams_are_stored_as_empty_strings(store: Store) -> None:
    recorder = UsageRecorder(store, clock=Clock())

    recorder.record("gpt-mystery", None, None)
    recorder.close()

    assert store.usage_rows(TODAY) == [UsageRow(TODAY, "gpt-mystery", "", "", "", "", 1)]


class BrokenStore(Store):
    def add_usage(self, rows) -> None:
        raise sqlite3.OperationalError("database is locked")

    def prune_usage(self, before_day: date) -> int:
        raise sqlite3.OperationalError("database is locked")


FLUSH_FAILED = "sundial: usage flush failed; unwritten counts stay buffered"


def test_a_failing_store_is_logged_and_never_raises(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    recorder = UsageRecorder(BrokenStore(store.path), clock=Clock(), flush_calls=1, spawn=inline)

    recorder.record("gpt-4", None, GPT4)
    recorder.flush()

    assert caplog.messages == [FLUSH_FAILED] * 2


def test_close_at_exit_with_a_failing_store_logs_and_never_raises(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    idle = UsageRecorder(BrokenStore(store.path), clock=Clock())
    busy = UsageRecorder(BrokenStore(store.path), clock=Clock())
    busy.record("gpt-4", None, GPT4)

    idle.close()  # nothing buffered: only the prune runs, and it fails
    busy.close()

    assert caplog.messages == [FLUSH_FAILED] * 2


@pytest.mark.parametrize(
    "error",
    [sqlite3.OperationalError("database is locked"), OSError("disk I/O error")],
    ids=lambda error: type(error).__name__,
)
def test_counts_survive_a_failed_write_and_go_out_with_the_next_flush(store: Store, error: Exception) -> None:
    class FailsOnce(Store):
        failed = False

        def add_usage(self, rows) -> None:
            if not self.failed:
                self.failed = True
                raise error
            super().add_usage(rows)

    flaky = FailsOnce(store.path)
    recorder = UsageRecorder(flaky, clock=Clock(), flush_calls=2, spawn=inline)

    recorder.record("gpt-4", None, GPT4)
    recorder.record("gpt-4", None, GPT4)
    assert flaky.failed and store.usage_rows(TODAY) == []
    recorder.record("gpt-4", None, GPT4)
    recorder.flush()

    assert store.usage_rows(TODAY) == [row(TODAY, 3)]


def test_a_bad_record_call_is_logged_and_never_raises(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    recorder = UsageRecorder(store, clock=Clock())

    recorder.record("gpt-4", None, ("not", "a key"))  # type: ignore[arg-type]

    assert "sundial: could not record usage for gpt-4" in caplog.messages


def test_a_model_name_longer_than_the_limit_is_not_recorded(store: Store) -> None:
    recorder = UsageRecorder(store, clock=Clock())

    recorder.record("x" * 300, None, None)
    recorder.record("y" * MAX_MODEL_NAME_LENGTH, None, None)
    recorder.flush()

    assert [row.public_model for row in store.usage_rows(TODAY)] == ["y" * MAX_MODEL_NAME_LENGTH]


@pytest.mark.parametrize("name", ["\ud800", "gpt-4\n", "gpt-4\x1b[31m", "gpt\t4", "gpt-4\x00"], ids=repr)
def test_a_model_name_that_is_not_printable_is_not_recorded(store: Store, name: str) -> None:
    recorder = UsageRecorder(store, clock=Clock())

    recorder.record(name, None, None)
    recorder.record("modèle-été", None, None)
    recorder.flush()

    assert [row.public_model for row in store.usage_rows(TODAY)] == ["modèle-été"]


def test_a_team_that_is_not_printable_is_not_recorded(store: Store) -> None:
    recorder = UsageRecorder(store, clock=Clock())

    recorder.record("gpt-4", "pay\nments", GPT4)
    recorder.record("gpt-4", "\udcff", GPT4)
    recorder.record("gpt-4", "équipe", GPT4)
    recorder.flush()

    assert store.usage_rows(TODAY) == [row(TODAY, 1, team="équipe")]


UNWRITABLE = [
    UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed"),
    sqlite3.InterfaceError("Error binding parameter 2"),
    sqlite3.ProgrammingError("Error binding parameter 2"),
]


def poisoned_store(path, error: Exception) -> Store:
    """A store that raises `error` for any write that includes the public model `poison`."""

    class Poisoned(Store):
        attempts = 0

        def add_usage(self, rows) -> None:
            rows = list(rows)
            if any(row.public_model == "poison" for row in rows):
                self.attempts += 1
                raise error
            super().add_usage(rows)

    return Poisoned(path)


def warnings_in(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]


@pytest.mark.parametrize("error", UNWRITABLE, ids=lambda error: type(error).__name__)
def test_a_row_that_can_never_be_written_is_dropped_and_the_rest_are_written(
    store: Store, error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    poisoned = poisoned_store(store.path, error)
    recorder = UsageRecorder(poisoned, clock=Clock())

    recorder.record("gpt-4", None, GPT4)
    recorder.record("poison", None, None)
    recorder.record("gpt-4", None, GPT4)
    recorder.record("gpt-mystery", None, None)
    recorder.flush()
    recorder.flush()  # the buffer was cleared, so the poisoned row is not tried again

    assert {(r.public_model, r.calls) for r in store.usage_rows(TODAY)} == {("gpt-4", 2), ("gpt-mystery", 1)}
    assert poisoned.attempts == 2  # once in the batch, once on its own
    assert warnings_in(caplog) == ["sundial: dropped 1 usage row that could not be written"]


def test_a_transient_failure_while_writing_row_by_row_keeps_the_unwritten_rows(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    class LockedAfterThePoison(Store):
        locked = True

        def add_usage(self, rows) -> None:
            rows = list(rows)
            if any(row.public_model == "poison" for row in rows):
                raise UNWRITABLE[0]
            if self.locked:
                raise sqlite3.OperationalError("database is locked")
            super().add_usage(rows)

    flaky = LockedAfterThePoison(store.path)
    recorder = UsageRecorder(flaky, clock=Clock())

    recorder.record("poison", None, None)
    recorder.record("gpt-4", None, GPT4)
    recorder.flush()
    assert store.usage_rows(TODAY) == []
    flaky.locked = False
    recorder.flush()

    assert store.usage_rows(TODAY) == [row(TODAY, 1)]
    assert warnings_in(caplog) == ["sundial: dropped 1 usage row that could not be written"]
    assert FLUSH_FAILED in caplog.messages


def test_a_full_buffer_drops_new_models_and_warns_once_per_flush_cycle(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    recorder = UsageRecorder(store, clock=Clock(), max_pending=2)

    for name in ("model-a", "model-b", "model-c", "model-a", "model-d", "model-c"):
        recorder.record(name, None, None)
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    recorder.flush()

    assert {(row.public_model, row.calls) for row in store.usage_rows(TODAY)} == {
        ("model-a", 2),
        ("model-b", 1),
    }
    assert warnings == [
        "sundial: usage buffer is full (2 entries); new models are not counted until it flushes"
    ]
    recorder.record("model-c", None, None)
    recorder.flush()
    assert ("model-c", 1) in {(row.public_model, row.calls) for row in store.usage_rows(TODAY)}


def test_flush_prunes_rows_older_than_the_retention(store: Store) -> None:
    store.add_usage([row(TODAY - timedelta(days=91), 1), row(TODAY - timedelta(days=90), 1)])
    recorder = UsageRecorder(store, clock=Clock(), retention_days=90)

    recorder.flush()

    assert store.usage_rows(date.min) == [row(TODAY - timedelta(days=90), 1)]


class Watched(Store):
    """Remembers which thread ran each usage write and every prune cutoff."""

    def __init__(self, path) -> None:
        self.writers: list[threading.Thread] = []
        self.prunes: list[date] = []
        self.written = threading.Event()
        super().__init__(path)

    def add_usage(self, rows) -> None:
        super().add_usage(rows)
        self.writers.append(threading.current_thread())
        self.written.set()

    def prune_usage(self, before_day: date) -> int:
        self.prunes.append(before_day)
        return super().prune_usage(before_day)


def test_a_due_flush_is_written_on_a_daemon_thread_by_default(store: Store) -> None:
    watched = Watched(store.path)
    recorder = UsageRecorder(watched, clock=Clock(), flush_calls=2)

    recorder.record("gpt-4", None, GPT4)
    recorder.record("gpt-4", None, GPT4)

    assert watched.written.wait(5)
    assert watched.writers[0] is not threading.current_thread()
    assert watched.writers[0].daemon
    assert store.usage_rows(TODAY) == [row(TODAY, 2)]


def test_a_due_flush_is_handed_to_spawn_and_not_written_by_the_recording_call(store: Store) -> None:
    jobs: list = []
    recorder = UsageRecorder(store, clock=Clock(), flush_calls=2, spawn=jobs.append)

    recorder.record("gpt-4", None, GPT4)
    recorder.record("gpt-4", None, GPT4)

    assert len(jobs) == 1
    assert store.usage_rows(TODAY) == []
    jobs[0]()
    assert store.usage_rows(TODAY) == [row(TODAY, 2)]


def test_only_one_flush_is_in_flight_and_later_counts_wait_for_the_next_due_point(store: Store) -> None:
    jobs: list = []
    recorder = UsageRecorder(store, clock=Clock(), flush_calls=1, spawn=jobs.append)

    recorder.record("model-a", None, None)  # due: model-a goes to a flush
    recorder.record("model-b", None, None)  # due, but a flush is in flight: model-b stays buffered
    assert len(jobs) == 1
    jobs[0]()
    assert [r.public_model for r in store.usage_rows(TODAY)] == ["model-a"]
    recorder.record("model-c", None, None)

    assert len(jobs) == 2
    jobs[1]()
    assert {(r.public_model, r.calls) for r in store.usage_rows(TODAY)} == {
        ("model-a", 1),
        ("model-b", 1),
        ("model-c", 1),
    }


def test_a_flush_that_cannot_start_a_thread_keeps_its_counts_buffered(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    def no_threads(job) -> None:
        raise RuntimeError("can't start new thread")

    recorder = UsageRecorder(store, clock=Clock(), flush_calls=1, spawn=no_threads)

    recorder.record("gpt-4", None, GPT4)
    recorder.record("gpt-4", None, GPT4)
    recorder.close()

    assert store.usage_rows(TODAY) == [row(TODAY, 2)]
    assert "sundial: could not start a usage flush; counts stay buffered" in caplog.messages


def test_usage_is_pruned_once_per_utc_day(store: Store) -> None:
    watched = Watched(store.path)
    clock = Clock()
    recorder = UsageRecorder(watched, clock=clock, flush_calls=1, spawn=inline, retention_days=90)

    for _ in range(3):
        recorder.record("gpt-4", None, GPT4)
    recorder.flush()
    assert watched.prunes == [TODAY - timedelta(days=90)]
    clock.now = NOW + timedelta(days=1)
    recorder.record("gpt-4", None, GPT4)
    recorder.record("gpt-4", None, GPT4)

    assert watched.prunes == [TODAY - timedelta(days=90), TODAY - timedelta(days=89)]


def test_a_failed_prune_is_tried_again_at_the_next_flush(store: Store) -> None:
    class PruneFailsOnce(Watched):
        def prune_usage(self, before_day: date) -> int:
            if not self.prunes:
                self.prunes.append(before_day)
                raise sqlite3.OperationalError("database is locked")
            return super().prune_usage(before_day)

    flaky = PruneFailsOnce(store.path)
    recorder = UsageRecorder(flaky, clock=Clock())

    recorder.flush()
    recorder.flush()
    recorder.flush()

    assert flaky.prunes == [TODAY - timedelta(days=90)] * 2


def test_close_writes_on_the_calling_thread(store: Store) -> None:
    def no_spawn(job) -> None:
        raise AssertionError("close must not hand its flush to another thread")

    watched = Watched(store.path)
    recorder = UsageRecorder(watched, clock=Clock(), spawn=no_spawn)

    recorder.record("gpt-4", None, GPT4)
    recorder.close()

    assert watched.writers == [threading.current_thread()]
    assert store.usage_rows(TODAY) == [row(TODAY, 1)]


def test_close_waits_for_a_flush_in_flight_then_writes_the_rest(store: Store) -> None:
    def shortly(job) -> None:
        threading.Timer(0.3, job).start()

    recorder = UsageRecorder(store, clock=Clock(), flush_calls=1, spawn=shortly)

    recorder.record("model-a", None, None)  # handed to a flush that starts 0.3 s later
    recorder.record("model-b", None, None)  # stays buffered while that flush is in flight
    recorder.close()

    assert {(r.public_model, r.calls) for r in store.usage_rows(TODAY)} == {("model-a", 1), ("model-b", 1)}


def test_the_store_uses_write_ahead_logging(store: Store) -> None:
    with closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_usage_writes_wait_at_most_one_second_for_a_locked_database(tmp_path) -> None:
    class Timeouts(Store):
        def __init__(self, path) -> None:
            self.timeouts: list[int] = []
            super().__init__(path)

        def _connect(self, *args, **kwargs) -> sqlite3.Connection:
            conn = super()._connect(*args, **kwargs)
            self.timeouts.append(conn.execute("PRAGMA busy_timeout").fetchone()[0])
            return conn

    timed = Timeouts(tmp_path / "usage.db")
    timed.timeouts.clear()

    timed.add_usage([row(TODAY, 1)])
    timed.prune_usage(TODAY - timedelta(days=90))

    assert timed.timeouts == [1000, 1000]
