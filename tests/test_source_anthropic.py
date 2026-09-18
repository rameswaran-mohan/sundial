from datetime import date

import pytest

from sundial_llm.models import LifecycleState, SourceFormatError
from sundial_llm.sources import anthropic


@pytest.fixture
def records(load_fixture, index_records):
    return index_records(anthropic.parse(load_fixture("anthropic_deprecations.md")).records)


def test_retired_model_merges_status_row_with_its_history_entry(records) -> None:
    rec = records[("claude-opus-4-1-20250805", "")]

    assert rec.platform == "anthropic"
    assert rec.state is LifecycleState.RETIRED
    assert rec.deprecated_on == date(2026, 6, 5)
    assert rec.announced_on == date(2026, 6, 5)
    assert rec.retire_on == date(2026, 8, 5)
    assert rec.retire_on_is_floor is False
    assert rec.replacements == ("claude-opus-4-8",)


def test_active_model_carries_a_floor_date_not_a_retirement(records) -> None:
    rec = records[("claude-sonnet-5", "")]

    assert rec.state is LifecycleState.ACTIVE
    assert rec.retire_on == date(2027, 6, 30)
    assert rec.retire_on_is_floor is True
    assert rec.deprecated_on is None


def test_models_found_only_in_history_are_still_recorded(records) -> None:
    rec = records[("claude-2.0", "")]

    assert rec.retire_on == date(2025, 7, 21)
    assert rec.replacements == ("claude-opus-4-8",)
    assert rec.effective_state(date(2026, 9, 19)) is LifecycleState.RETIRED


def test_parameter_deprecation_table_is_not_read_as_models(records) -> None:
    assert all(model_id.startswith("claude") for model_id, _ in records)


def test_page_with_no_model_tables_raises() -> None:
    with pytest.raises(SourceFormatError):
        anthropic.parse("# Model deprecations\n\nNothing here.\n")
