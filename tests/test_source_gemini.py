from datetime import date

import pytest

from sundial_llm.models import LifecycleState, SourceFormatError
from sundial_llm.sources import gemini


@pytest.fixture
def records(load_fixture, index_records):
    return index_records(gemini.parse(load_fixture("gemini_deprecations.md")).records)


def test_model_with_a_shutdown_date_is_deprecated_with_a_firm_date(records) -> None:
    rec = records[("gemini-2.0-flash", "")]

    assert rec.platform == "gemini"
    assert rec.state is LifecycleState.DEPRECATED
    assert rec.released_on == date(2025, 2, 5)
    assert rec.retire_on == date(2026, 6, 1)
    assert rec.retire_on_is_floor is False
    assert rec.replacements == ("gemini-3.6-flash",)
    assert rec.vendor == "Gemini 2.0 models"


def test_model_with_no_shutdown_date_is_active(records) -> None:
    rec = records[("gemini-3.8-flash", "")]

    assert rec.state is LifecycleState.ACTIVE
    assert rec.retire_on is None


def test_rows_after_the_preview_divider_are_preview(records) -> None:
    assert records[("gemini-3.1-pro-preview", "")].state is LifecycleState.PREVIEW


def test_preview_model_with_a_shutdown_date_is_deprecated(records) -> None:
    rec = records[("gemini-3-pro-preview", "")]

    assert rec.state is LifecycleState.DEPRECATED
    assert rec.retire_on == date(2026, 3, 9)


def test_divider_rows_are_not_models(records) -> None:
    assert not any(model_id.lower().startswith("preview") for model_id, _ in records)


def test_page_with_no_model_tables_raises() -> None:
    with pytest.raises(SourceFormatError):
        gemini.parse("## Gemini 3 models\n\nNothing here.\n")
