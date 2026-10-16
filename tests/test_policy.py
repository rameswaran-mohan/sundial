from datetime import date

import pytest

from sundial_llm.headers import decision_headers
from sundial_llm.matching import Match
from sundial_llm.models import LifecycleRecord, LifecycleState
from sundial_llm.policy import ALLOW, DEFAULT_POLICY, Action, Mode, load_policy

TODAY = date(2026, 9, 19)


def match(approximate: bool = False, **extra: object) -> Match:
    base = {
        "platform": "openai",
        "model_id": "gpt-old",
        "state": LifecycleState.DEPRECATED,
        "source_url": "u",
        "evidence": "e",
    }
    return Match(LifecycleRecord(**{**base, **extra}), approximate)


POLICY = load_policy(
    {
        "rules": [
            {"name": "block-retired", "when": {"state": "retired"}, "action": "block", "mode": "apply"},
            {
                "name": "redirect-week",
                "when": {"days_until_retirement_lte": 7},
                "action": "redirect",
                "mode": "apply",
            },
            {"name": "warn-90", "when": {"days_until_retirement_lte": 90}, "action": "warn"},
        ],
        "successors": {"public-old": "public-new"},
    }
)


def test_no_match_is_allowed() -> None:
    decision = POLICY.decide(None, TODAY, "anything")

    assert decision.action is Action.ALLOW
    assert decision.enforced is False


def test_model_outside_every_rule_is_allowed() -> None:
    decision = POLICY.decide(match(retire_on=date(2027, 9, 1)), TODAY, "m")

    assert decision.action is Action.ALLOW


def test_matched_model_that_no_rule_hits_still_carries_its_record_key() -> None:
    decision = POLICY.decide(match(retire_on=date(2027, 9, 1)), TODAY, "m")

    assert decision.action is Action.ALLOW
    assert decision.key == ("openai", "gpt-old", "")


def test_unmatched_model_gets_the_shared_allow_with_no_key() -> None:
    decision = POLICY.decide(None, TODAY, "m")

    assert decision is ALLOW
    assert decision.key is None


def test_first_matching_rule_wins() -> None:
    decision = POLICY.decide(match(retire_on=date(2026, 9, 1)), TODAY, "m")

    assert (decision.rule, decision.action, decision.enforced) == ("block-retired", Action.BLOCK, True)
    assert decision.state is LifecycleState.RETIRED


def test_warn_rule_reports_days_left() -> None:
    decision = POLICY.decide(match(retire_on=date(2026, 10, 19)), TODAY, "m")

    assert (decision.rule, decision.action, decision.days_left) == ("warn-90", Action.WARN, 30)
    assert decision.enforced is False
    assert decision.key == ("openai", "gpt-old", "")


def test_floor_dates_never_trigger_day_based_rules() -> None:
    decision = POLICY.decide(
        match(state=LifecycleState.ACTIVE, retire_on=date(2026, 9, 25), retire_on_is_floor=True), TODAY, "m"
    )

    assert decision.action is Action.ALLOW


def test_redirect_uses_the_configured_public_successor() -> None:
    decision = POLICY.decide(match(retire_on=date(2026, 9, 22)), TODAY, "public-old")

    assert (decision.action, decision.target, decision.enforced) == (Action.REDIRECT, "public-new", True)


def test_redirect_without_a_configured_successor_degrades_to_a_warning() -> None:
    decision = POLICY.decide(match(retire_on=date(2026, 9, 22)), TODAY, "no-successor")

    assert (decision.action, decision.enforced, decision.target) == (Action.WARN, False, None)


def test_approximate_matches_are_never_enforced() -> None:
    decision = POLICY.decide(match(approximate=True, retire_on=date(2026, 9, 1)), TODAY, "m")

    assert decision.action is Action.BLOCK
    assert decision.enforced is False
    assert decision.approximate is True


def test_propose_is_the_default_mode_and_is_never_enforced() -> None:
    policy = load_policy({"rules": [{"name": "b", "when": {"state": "retired"}, "action": "block"}]})

    decision = policy.decide(match(retire_on=date(2026, 9, 1)), TODAY, "m")

    assert (decision.mode, decision.action, decision.enforced) == (Mode.PROPOSE, Action.BLOCK, False)


def test_platform_and_glob_filters_narrow_a_rule() -> None:
    policy = load_policy(
        {
            "rules": [
                {"name": "g", "when": {"platform": "gemini", "model_glob": "gemini-2.*"}, "action": "warn"}
            ]
        }
    )

    assert policy.decide(match(), TODAY, "m").action is Action.ALLOW
    assert (
        policy.decide(match(platform="gemini", model_id="gemini-2.0-flash"), TODAY, "m").action is Action.WARN
    )


def test_default_policy_only_warns() -> None:
    retired = DEFAULT_POLICY.decide(match(retire_on=date(2026, 9, 1)), TODAY, "m")
    soon = DEFAULT_POLICY.decide(match(retire_on=date(2026, 11, 1)), TODAY, "m")

    assert retired.action is Action.WARN and retired.enforced is False
    assert soon.action is Action.WARN


def test_unknown_action_in_config_is_rejected() -> None:
    with pytest.raises(ValueError):
        load_policy({"rules": [{"name": "x", "when": {}, "action": "explode"}]})


def test_headers_follow_the_deprecation_and_sunset_rfcs() -> None:
    decision = POLICY.decide(
        match(
            retire_on=date(2026, 10, 19),
            deprecated_on=date(2026, 4, 1),
            replacements=("gpt-new", "gpt-newer"),
        ),
        TODAY,
        "m",
    )

    headers = decision_headers(decision)

    assert headers["Deprecation"] == "@1775001600"
    assert headers["Sunset"] == "Mon, 19 Oct 2026 00:00:00 GMT"
    assert headers["X-Sundial-State"] == "deprecated"
    assert headers["X-Sundial-Successor"] == "gpt-new, gpt-newer"
    assert headers["X-Sundial-Rule"] == "warn-90"
    assert headers["X-Sundial-Days-Left"] == "30"


def test_redirect_headers_name_the_public_target_and_flag_approximation() -> None:
    headers = decision_headers(
        POLICY.decide(match(approximate=True, retire_on=date(2026, 9, 22)), TODAY, "public-old")
    )

    assert headers["X-Sundial-Successor"] == "public-new"
    assert headers["X-Sundial-Approximate"] == "true"


def test_allowed_requests_get_no_headers() -> None:
    assert decision_headers(POLICY.decide(None, TODAY, "m")) == {}
