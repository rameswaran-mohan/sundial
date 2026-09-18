from datetime import date

from sundial_llm.models import LifecycleRecord, LifecycleState, merge_records


def record(**overrides: object) -> LifecycleRecord:
    base: dict[str, object] = {
        "platform": "openai",
        "model_id": "gpt-x",
        "state": LifecycleState.ACTIVE,
        "source_url": "https://example.test/doc",
        "evidence": "| row |",
    }
    return LifecycleRecord(**{**base, **overrides})


def test_key_is_platform_model_and_version() -> None:
    assert record().key == ("openai", "gpt-x", "")
    assert record(platform="azure", version="2024-05-13").key == ("azure", "gpt-x", "2024-05-13")


def test_firm_retirement_date_in_the_past_means_retired() -> None:
    rec = record(state=LifecycleState.DEPRECATED, retire_on=date(2026, 10, 1))

    assert rec.effective_state(date(2026, 9, 30)) is LifecycleState.DEPRECATED
    assert rec.effective_state(date(2026, 10, 1)) is LifecycleState.RETIRED


def test_floor_date_in_the_past_does_not_mean_retired() -> None:
    rec = record(retire_on=date(2026, 1, 1), retire_on_is_floor=True)

    assert rec.effective_state(date(2026, 6, 1)) is LifecycleState.ACTIVE


def test_days_until_retirement_is_none_for_floors_and_missing_dates() -> None:
    assert record().days_until_retirement(date(2026, 1, 1)) is None
    assert (
        record(retire_on=date(2026, 1, 11), retire_on_is_floor=True).days_until_retirement(date(2026, 1, 1))
        is None
    )
    assert record(retire_on=date(2026, 1, 11)).days_until_retirement(date(2026, 1, 1)) == 10


def test_merge_keeps_the_more_severe_state_and_fills_gaps() -> None:
    from_changelog = record(released_on=date(2025, 1, 1))
    from_deprecations = record(
        state=LifecycleState.DEPRECATED,
        retire_on=date(2026, 10, 1),
        replacements=("gpt-y",),
        source_url="https://example.test/deprecations",
    )

    merged = merge_records(from_changelog, from_deprecations)

    assert merged.state is LifecycleState.DEPRECATED
    assert merged.retire_on == date(2026, 10, 1)
    assert merged.released_on == date(2025, 1, 1)
    assert merged.source_url == "https://example.test/deprecations"


def test_merge_is_symmetric_and_unions_replacements_in_order() -> None:
    a = record(state=LifecycleState.DEPRECATED, replacements=("one", "two"))
    b = record(state=LifecycleState.DEPRECATED, replacements=("two", "three"))

    assert merge_records(a, b).replacements == ("one", "two", "three")
    assert merge_records(b, a).state is LifecycleState.DEPRECATED
