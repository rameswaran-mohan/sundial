from datetime import date

import pytest

from sundial_llm.models import LifecycleState, SourceFormatError
from sundial_llm.sources import openai_deprecations


@pytest.fixture
def records(load_fixture, index_records):
    return index_records(openai_deprecations.parse(load_fixture("openai_deprecations.md")).records)


def test_row_carries_shutdown_announcement_and_replacement(records) -> None:
    rec = records[("gpt-5.4-cyber", "")]

    assert rec.platform == "openai"
    assert rec.state is LifecycleState.DEPRECATED
    assert rec.retire_on == date(2026, 10, 1)
    assert rec.retire_on_is_floor is False
    assert rec.announced_on == date(2026, 9, 11)
    assert rec.replacements == ("gpt-5.6-cyber",)


def test_cell_listing_a_snapshot_and_its_aliases_yields_one_record_each(records) -> None:
    assert records[("gpt-4-0613", "")].retire_on == date(2026, 10, 23)
    assert records[("gpt-4", "")].retire_on == date(2026, 10, 23)
    assert records[("gpt-4", "")].replacements == ("gpt-5.6-sol",)


def test_notes_inside_the_replacement_cell_are_not_replacements(records) -> None:
    assert records[("o1-pro", "")].replacements == ("gpt-5.6-sol",)


def test_either_or_replacements_are_both_kept(records) -> None:
    assert records[("whisper-1", "")].replacements == ("gpt-live-transcribe", "gpt-transcribe")


def test_four_column_price_tables_read_the_last_column_as_replacement(records) -> None:
    rec = records[("gpt-4-32k", "")]

    assert rec.retire_on == date(2025, 6, 6)
    assert rec.replacements == ("gpt-4o",)


def test_past_deprecations_are_recorded_with_their_announcement_date(records) -> None:
    rec = records[("chatgpt-4o-latest", "")]

    assert rec.announced_on == date(2025, 11, 18)
    assert rec.retire_on == date(2026, 2, 17)


def test_endpoint_and_system_deprecations_are_not_models(records) -> None:
    assert not any(model_id.startswith(("/", "v1")) for model_id, _ in records)


def test_page_with_no_shutdown_tables_raises() -> None:
    with pytest.raises(SourceFormatError):
        openai_deprecations.parse("# Deprecations\n\n## Overview\n")
