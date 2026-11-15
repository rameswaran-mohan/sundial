from datetime import date

import pytest

from sundial_llm.models import LifecycleState, SourceFormatError
from sundial_llm.sources import gemini


@pytest.fixture
def records(load_fixture, index_records):
    """The page as it was served until 2026-11-04: pipe tables with divider rows."""
    return index_records(gemini.parse(load_fixture("gemini_deprecations.md")).records)


@pytest.fixture
def markdoc_records(load_fixture, index_records):
    """The page as it is served since 2026-11-04: Markdoc table blocks under status subheadings."""
    return index_records(gemini.parse(load_fixture("gemini_deprecations_markdoc.md")).records)


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


def test_markdoc_deprecated_model_has_release_date_firm_shutdown_and_replacement(markdoc_records) -> None:
    rec = markdoc_records[("gemini-2.0-flash", "")]

    assert rec.platform == "gemini"
    assert rec.state is LifecycleState.DEPRECATED
    assert rec.released_on == date(2025, 2, 5)
    assert rec.retire_on == date(2026, 6, 1)
    assert rec.retire_on_is_floor is False
    assert rec.replacements == ("gemini-3.6-flash",)
    assert rec.evidence == "| `gemini-2.0-flash` | February 5, 2025 | June 1, 2026 | `gemini-3.6-flash` |"


def test_markdoc_vendor_is_the_family_heading_without_the_id_suffix(markdoc_records) -> None:
    assert markdoc_records[("gemini-2.0-flash", "")].vendor == "Gemini 2.0 models"
    assert markdoc_records[("gemini-3.1-pro-preview", "")].vendor == "Gemini 3 models"
    assert markdoc_records[("gemini-3.6-flash", "")].vendor == "Gemini 3 models"
    assert not any("{%" in (rec.vendor or "") for rec in markdoc_records.values())


def test_markdoc_model_with_no_shutdown_date_is_active(markdoc_records) -> None:
    rec = markdoc_records[("gemini-3.6-flash", "")]

    assert rec.state is LifecycleState.ACTIVE
    assert rec.released_on == date(2026, 7, 21)
    assert rec.retire_on is None


def test_markdoc_empty_replacement_cell_gives_no_replacements(markdoc_records) -> None:
    assert markdoc_records[("gemini-3.6-flash", "")].replacements == ()


def test_markdoc_empty_release_date_cell_gives_no_release_date(markdoc_records) -> None:
    rec = markdoc_records[("embedding-gecko-001", "")]

    assert rec.released_on is None
    assert rec.retire_on == date(2025, 10, 30)
    assert rec.replacements == ("gemini-embedding-2",)


def test_markdoc_rows_under_a_preview_subheading_are_preview(markdoc_records) -> None:
    assert markdoc_records[("gemini-3.1-pro-preview", "")].state is LifecycleState.PREVIEW
    assert markdoc_records[("gemini-robotics-er-2-preview", "")].state is LifecycleState.PREVIEW
    assert markdoc_records[("gemini-robotics-er-2-preview", "")].vendor == "Robotics models"


def test_markdoc_preview_row_with_a_shutdown_date_is_deprecated(markdoc_records) -> None:
    rec = markdoc_records[("gemini-3.1-flash-tts-preview", "")]

    assert rec.state is LifecycleState.DEPRECATED
    assert rec.retire_on == date(2026, 12, 18)


def test_markdoc_rows_outside_a_preview_subheading_are_not_preview(markdoc_records) -> None:
    assert markdoc_records[("gemini-4-hydrogen", "")].state is LifecycleState.ACTIVE
    assert markdoc_records[("lyria-3.5", "")].state is LifecycleState.ACTIVE


def test_markdoc_agent_tables_are_skipped_like_before(markdoc_records) -> None:
    assert not any(model_id.startswith(("antigravity", "deep-research")) for model_id, _ in markdoc_records)


def test_markdoc_page_with_no_tables_raises() -> None:
    page = (
        '# Gemini deprecations\n\n## Gemini 3 models  {% id="gemini-3-models" %}\n\n'
        '{% callout type="note" %}\n  `gemini-3-pro` is retired.\n{% /callout %}\n'
    )

    with pytest.raises(SourceFormatError):
        gemini.parse(page)
