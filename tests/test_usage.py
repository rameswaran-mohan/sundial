import logging
import sqlite3
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
    recorder = UsageRecorder(store, clock=Clock(), flush_calls=5)

    for _ in range(4):
        recorder.record("gpt-4", "payments", GPT4)
    assert store.usage_rows(TODAY) == []
    recorder.record("gpt-4", "payments", GPT4)

    assert store.usage_rows(TODAY) == [row(TODAY, 5, team="payments")]


def test_a_call_after_flush_seconds_flushes_everything_buffered(store: Store) -> None:
    clock = Clock()
    recorder = UsageRecorder(store, clock=clock, flush_seconds=30)

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
    recorder = UsageRecorder(BrokenStore(store.path), clock=Clock(), flush_calls=1)

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
    recorder = UsageRecorder(flaky, clock=Clock(), flush_calls=2)

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
