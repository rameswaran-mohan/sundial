from datetime import date

import pytest

from sundial_llm.models import LifecycleState, SourceFormatError
from sundial_llm.sources import azure


@pytest.fixture
def result(load_fixture):
    return azure.parse(load_fixture("azure_retirement_schedule.md"))


@pytest.fixture
def records(result, index_records):
    return index_records(result.records)


def test_deprecated_row_carries_version_date_replacement_and_vendor(records) -> None:
    rec = records[("gpt-4o", "2024-05-13")]

    assert rec.platform == "azure"
    assert rec.state is LifecycleState.DEPRECATED
    assert rec.retire_on == date(2026, 10, 1)
    assert rec.retire_on_is_floor is False
    assert rec.replacements == ("gpt-5.1",)
    assert rec.vendor == "Azure OpenAI"
    assert "gpt-4o" in rec.evidence
    assert rec.source_url == azure.SOURCE_URL


def test_ga_lifecycle_means_active(records) -> None:
    rec = records[("gpt-5", "2025-08-07")]

    assert rec.state is LifecycleState.ACTIVE
    assert rec.retire_on == date(2027, 2, 9)


def test_dash_cells_mean_no_date_and_no_replacement(records) -> None:
    rec = records[("FLUX-1.1-pro", "1")]

    assert rec.retire_on is None
    assert rec.replacements == ()


def test_missing_version_is_none_and_vendor_comes_from_the_section(records) -> None:
    rec = records[("claude-opus-4-1", "")]

    assert rec.version is None
    assert rec.state is LifecycleState.RETIRED
    assert rec.vendor == "Anthropic"


def test_footnote_markers_are_stripped_from_replacements(records) -> None:
    rec = records[("Cohere-command-a-plus-05-2026", "1")]

    assert rec.replacements == (
        "coherelabs-command-a-plus-05-2026-fp8",
        "coherelabs-command-a-plus-05-2026-bf16",
        "coherelabs-command-a-plus-05-2026-w4a4",
    )


def test_parenthetical_note_is_not_part_of_the_model_id(records) -> None:
    assert ("claude-mythos-preview", "") in records


def test_fine_tuned_tables_do_not_overwrite_base_model_dates(records) -> None:
    assert records[("gpt-4o", "2024-08-06")].retire_on == date(2027, 4, 14)


def test_duplicate_rows_keep_the_earliest_retirement_and_warn(result, records) -> None:
    assert records[("gpt-realtime-mini", "2025-10-06")].retire_on == date(2026, 9, 21)
    assert any("gpt-realtime-mini" in warning for warning in result.warnings)


def test_page_with_no_lifecycle_tables_raises() -> None:
    with pytest.raises(SourceFormatError):
        azure.parse("# Model retirement schedule\n\nNothing here.\n")
