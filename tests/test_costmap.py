from datetime import date

from sundial_llm.costmap import build_patch
from sundial_llm.models import LifecycleRecord, LifecycleState

TODAY = date(2026, 9, 19)


def rec(platform: str, model_id: str, version: str | None = None, **extra: object) -> LifecycleRecord:
    base = {
        "state": LifecycleState.DEPRECATED,
        "source_url": f"https://docs.test/{platform}",
        "evidence": "e",
    }
    return LifecycleRecord(platform=platform, model_id=model_id, version=version, **{**base, **extra})


RECORDS = {
    r.key: r
    for r in (
        rec("openai", "gpt-4-0613", retire_on=date(2026, 10, 23)),
        rec("openai", "gpt-same", retire_on=date(2026, 5, 1)),
        rec(
            "anthropic",
            "claude-sonnet-5",
            state=LifecycleState.ACTIVE,
            retire_on=date(2027, 6, 30),
            retire_on_is_floor=True,
        ),
        rec("azure", "gpt-4o", "2024-05-13", retire_on=date(2026, 10, 1)),
        rec("azure", "gpt-4o", "2024-08-06", retire_on=date(2027, 4, 14)),
        rec(
            "bedrock",
            "anthropic.claude-opus-4-1-20250805-v1:0",
            state=LifecycleState.LEGACY,
            retire_on=date(2027, 1, 8),
        ),
        rec("gemini", "gemini-2.0-flash", retire_on=date(2026, 6, 1)),
    )
}


def changes(cost_map: dict) -> dict[str, tuple[str | None, str]]:
    return {c.key: (c.old, c.new) for c in build_patch(cost_map, RECORDS, TODAY).changes}


def test_missing_date_is_proposed_from_the_provider_page() -> None:
    assert changes({"gpt-4-0613": {"litellm_provider": "openai"}}) == {"gpt-4-0613": (None, "2026-10-23")}


def test_stale_date_is_replaced_and_the_old_value_is_shown() -> None:
    cost_map = {"gpt-4-0613": {"litellm_provider": "openai", "deprecation_date": "2025-06-06"}}

    assert changes(cost_map) == {"gpt-4-0613": ("2025-06-06", "2026-10-23")}


def test_entry_that_already_agrees_is_left_alone() -> None:
    assert changes({"gpt-same": {"litellm_provider": "openai", "deprecation_date": "2026-05-01"}}) == {}


def test_floor_dates_are_never_written_as_deprecation_dates() -> None:
    assert changes({"claude-sonnet-5": {"litellm_provider": "anthropic"}}) == {}


def test_azure_key_with_a_version_matches_exactly_and_an_unversioned_one_is_skipped() -> None:
    cost_map = {
        "azure/gpt-4o-2024-05-13": {"litellm_provider": "azure"},
        "azure/gpt-4o": {"litellm_provider": "azure"},
    }

    assert changes(cost_map) == {"azure/gpt-4o-2024-05-13": (None, "2026-10-01")}


def test_bedrock_and_prefixed_gemini_keys_are_matched() -> None:
    cost_map = {
        "anthropic.claude-opus-4-1-20250805-v1:0": {"litellm_provider": "bedrock"},
        "gemini/gemini-2.0-flash": {"litellm_provider": "gemini"},
    }

    assert changes(cost_map) == {
        "anthropic.claude-opus-4-1-20250805-v1:0": (None, "2027-01-08"),
        "gemini/gemini-2.0-flash": (None, "2026-06-01"),
    }


def test_vertex_entries_are_skipped_because_the_match_is_only_approximate() -> None:
    assert changes({"gemini-2.0-flash": {"litellm_provider": "vertex_ai-language-models"}}) == {}


def test_unknown_models_other_providers_and_the_sample_spec_are_untouched() -> None:
    cost_map = {
        "sample_spec": {"deprecation_date": "date when the model becomes deprecated"},
        "gpt-mystery": {"litellm_provider": "openai"},
        "together_ai/some-model": {"litellm_provider": "together_ai"},
        "not-a-dict": "oops",
    }

    assert changes(cost_map) == {}


def test_patch_renders_as_a_mapping_litellm_maintainers_can_apply() -> None:
    patch = build_patch({"gpt-4-0613": {"litellm_provider": "openai"}}, RECORDS, TODAY)

    assert patch.as_mapping() == {"gpt-4-0613": {"deprecation_date": "2026-10-23"}}
    assert patch.changes[0].source_url == "https://docs.test/openai"


def test_ambiguous_source_rows_are_never_proposed_upstream() -> None:
    uncertain = rec("openai", "gpt-twice", retire_on=date(2026, 9, 21), ambiguous=True)

    patch = build_patch({"gpt-twice": {"litellm_provider": "openai"}}, {uncertain.key: uncertain}, TODAY)

    assert patch.changes == ()
