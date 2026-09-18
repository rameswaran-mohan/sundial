from datetime import date

import pytest

from sundial_llm.matching import UpstreamModel, find_record, parse_upstream
from sundial_llm.models import LifecycleRecord, LifecycleState

TODAY = date(2026, 9, 19)


def rec(platform: str, model_id: str, version: str | None = None, **extra: object) -> LifecycleRecord:
    base = {"state": LifecycleState.ACTIVE, "source_url": "u", "evidence": "e"}
    return LifecycleRecord(platform=platform, model_id=model_id, version=version, **{**base, **extra})


RECORDS = {
    r.key: r
    for r in (
        rec("openai", "gpt-4-0613", state=LifecycleState.DEPRECATED, retire_on=date(2026, 10, 23)),
        rec("anthropic", "claude-sonnet-4-5-20250929"),
        rec("bedrock", "anthropic.claude-opus-4-1-20250805-v1:0", state=LifecycleState.LEGACY),
        rec("gemini", "gemini-2.0-flash", state=LifecycleState.DEPRECATED, retire_on=date(2026, 6, 1)),
        rec("azure", "gpt-4o", "2024-05-13", state=LifecycleState.DEPRECATED, retire_on=date(2026, 10, 1)),
        rec("azure", "gpt-4o", "2024-08-06", state=LifecycleState.DEPRECATED, retire_on=date(2027, 4, 14)),
        rec("azure", "gpt-4o", "2024-01-01", state=LifecycleState.RETIRED, retire_on=date(2025, 1, 1)),
        rec("azure", "gpt-5", "2025-08-07", retire_on=date(2027, 2, 9)),
    )
}


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-4-0613", UpstreamModel("openai", "gpt-4-0613")),
        ("openai/gpt-4-0613", UpstreamModel("openai", "gpt-4-0613")),
        ("claude-sonnet-4-5-20250929", UpstreamModel("anthropic", "claude-sonnet-4-5-20250929")),
        ("gemini-2.0-flash", UpstreamModel("gemini", "gemini-2.0-flash")),
        ("bedrock/converse/us.anthropic.claude-x-v1:0", UpstreamModel("bedrock", "anthropic.claude-x-v1:0")),
        ("bedrock/invoke/anthropic.claude-x-v1:0", UpstreamModel("bedrock", "anthropic.claude-x-v1:0")),
        ("azure/my-deployment", UpstreamModel("azure", "my-deployment")),
    ],
)
def test_parse_upstream_normalises_gateway_model_strings(model: str, expected: UpstreamModel) -> None:
    assert parse_upstream(model) == expected


def test_base_model_is_kept_for_azure_because_deployment_names_are_arbitrary() -> None:
    upstream = parse_upstream("azure/prod-chat", base_model="azure/gpt-4o-2024-05-13")

    assert upstream == UpstreamModel("azure", "prod-chat", "gpt-4o-2024-05-13")


def test_exact_matches_are_not_approximate() -> None:
    match = find_record(RECORDS, parse_upstream("gpt-4-0613"), TODAY)

    assert match is not None
    assert match.record.model_id == "gpt-4-0613"
    assert match.approximate is False


def test_bedrock_region_prefix_does_not_prevent_a_match() -> None:
    match = find_record(RECORDS, parse_upstream("bedrock/us.anthropic.claude-opus-4-1-20250805-v1:0"), TODAY)

    assert match is not None and match.record.state is LifecycleState.LEGACY


def test_azure_base_model_with_a_version_matches_that_version_exactly() -> None:
    match = find_record(RECORDS, parse_upstream("azure/prod", "azure/gpt-4o-2024-08-06"), TODAY)

    assert match is not None
    assert match.record.version == "2024-08-06"
    assert match.approximate is False


def test_azure_without_a_version_takes_the_earliest_live_retirement_and_says_so() -> None:
    match = find_record(RECORDS, parse_upstream("azure/prod", "azure/gpt-4o"), TODAY)

    assert match is not None
    assert match.record.version == "2024-05-13"
    assert match.approximate is True


def test_azure_model_with_a_single_version_is_exact_even_without_one_given() -> None:
    match = find_record(RECORDS, parse_upstream("azure/prod", "gpt-5"), TODAY)

    assert match is not None and match.approximate is False


def test_azure_deployment_name_is_tried_when_no_base_model_is_set() -> None:
    match = find_record(RECORDS, parse_upstream("azure/gpt-5"), TODAY)

    assert match is not None and match.record.model_id == "gpt-5"


def test_vertex_gemini_models_match_the_gemini_page_approximately() -> None:
    match = find_record(RECORDS, parse_upstream("vertex_ai/gemini-2.0-flash"), TODAY)

    assert match is not None
    assert match.record.platform == "gemini"
    assert match.approximate is True


@pytest.mark.parametrize("model", ["gpt-unknown", "together_ai/meta-llama/Llama-3", "azure/mystery-deployment"])
def test_unknown_models_and_providers_do_not_match(model: str) -> None:
    assert find_record(RECORDS, parse_upstream(model), TODAY) is None
