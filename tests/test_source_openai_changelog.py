from datetime import date

import pytest

from sundial_llm.models import LifecycleState, SourceFormatError
from sundial_llm.sources import openai_changelog

INLINE = """# Changelog

## January, 2026

### Jan 9

Feature · Model: gpt-new · Model: gpt-new-mini · API: v1/responses

### Jan 2

Update · Model: gpt-only-updated

## December, 2025

### Dec 20

Feature · Model: gpt-new
"""


def test_feature_entry_in_the_real_changelog_becomes_an_active_model(load_fixture, index_records) -> None:
    records = index_records(openai_changelog.parse(load_fixture("openai_changelog.md")).records)

    rec = records[("gpt-6-astra", "")]
    assert rec.platform == "openai"
    assert rec.state is LifecycleState.ACTIVE
    assert rec.released_on == date(2026, 9, 3)


def test_models_named_only_in_update_entries_are_not_new_models(load_fixture, index_records) -> None:
    records = index_records(openai_changelog.parse(load_fixture("openai_changelog.md")).records)

    assert ("whisper-1", "") not in records


def test_every_model_on_a_feature_line_is_recorded(index_records) -> None:
    records = index_records(openai_changelog.parse(INLINE).records)

    assert ("gpt-new-mini", "") in records
    assert ("gpt-only-updated", "") not in records


def test_a_model_mentioned_twice_keeps_its_earliest_date_across_years(index_records) -> None:
    records = index_records(openai_changelog.parse(INLINE).records)

    assert records[("gpt-new", "")].released_on == date(2025, 12, 20)


def test_changelog_with_no_model_lines_raises() -> None:
    with pytest.raises(SourceFormatError):
        openai_changelog.parse("# Changelog\n\n## January, 2026\n\n### Jan 9\n\nFeature\n")
