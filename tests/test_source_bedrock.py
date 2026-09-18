from datetime import date

import pytest

from sundial_llm.models import LifecycleState, SourceFormatError
from sundial_llm.sources import bedrock


@pytest.fixture
def result(load_fixture):
    return bedrock.parse(load_fixture("bedrock_lifecycle_legacy.md"))


@pytest.fixture
def records(result, index_records):
    return index_records(result.records)


def test_block_becomes_a_legacy_record_with_both_dates_and_vendor(records) -> None:
    rec = records[("anthropic.claude-opus-4-1-20250805-v1:0", "")]

    assert rec.platform == "bedrock"
    assert rec.state is LifecycleState.LEGACY
    assert rec.deprecated_on == date(2026, 7, 8)
    assert rec.retire_on == date(2027, 1, 8)
    assert rec.retire_on_is_floor is False
    assert rec.vendor == "Anthropic"


def test_dates_written_inline_on_the_regions_line_are_read(records) -> None:
    rec = records[("anthropic.claude-3-haiku-20240307-v1:0", "")]

    assert rec.deprecated_on == date(2026, 3, 10)
    assert rec.retire_on == date(2026, 9, 10)


def test_block_with_shifted_labels_is_skipped_with_a_warning(result, records) -> None:
    assert ("cohere.command-r-v1:0", "") not in records
    assert any("cohere.command-r-v1:0" in warning for warning in result.warnings)


def test_well_formed_neighbour_of_the_broken_block_still_parses(records) -> None:
    assert records[("cohere.command-r-plus-v1:0", "")].retire_on == date(2026, 8, 19)


def test_page_with_no_model_blocks_raises() -> None:
    with pytest.raises(SourceFormatError):
        bedrock.parse("# Model lifecycle (Legacy)\n\nNothing here.\n")
