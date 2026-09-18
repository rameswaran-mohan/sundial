from datetime import date
from pathlib import Path

import pytest

from sundial_llm.config import Config, NewModelRule, load_config
from sundial_llm.events import Event, EventType
from sundial_llm.models import LifecycleRecord, LifecycleState
from sundial_llm.notify import notify_events, render_event, render_proposal
from sundial_llm.policy import DEFAULT_POLICY, Action


def rec(platform: str = "openai", model_id: str = "gpt-x", **extra: object) -> LifecycleRecord:
    base = {"state": LifecycleState.ACTIVE, "source_url": "https://docs.test/page", "evidence": "e"}
    return LifecycleRecord(platform=platform, model_id=model_id, **{**base, **extra})


def event(kind: EventType, after: LifecycleRecord, before: LifecycleRecord | None = None) -> Event:
    return Event(type=kind, key=after.key, before=before, after=after)


def test_missing_config_gives_safe_defaults(tmp_path: Path) -> None:
    config = load_config(None, env={})

    assert config.policy == DEFAULT_POLICY
    assert config.auto_scan_hours == 24
    assert config.db.name == "sundial.db"


def test_config_file_is_found_through_the_environment(tmp_path: Path) -> None:
    path = tmp_path / "sundial.yaml"
    path.write_text(
        "db: {db}\n"
        "auto_scan_hours: 0\n"
        "rules:\n"
        "  - name: block-retired\n"
        "    when: {{state: retired}}\n"
        "    action: block\n"
        "    mode: apply\n"
        "successors:\n"
        "  old: new\n"
        "notify:\n"
        "  slack_webhook: https://hooks.test/slack\n"
        "new_models:\n"
        "  - name: claude\n"
        "    platform: anthropic\n"
        "    model_glob: 'claude-sonnet-*'\n".format(db=(tmp_path / "x.db").as_posix()),
        encoding="utf-8",
    )

    config = load_config(None, env={"SUNDIAL_CONFIG": str(path)})

    assert config.db == tmp_path / "x.db"
    assert config.auto_scan_hours == 0
    assert config.policy.rules[0].action is Action.BLOCK
    assert config.policy.successors == {"old": "new"}
    assert config.notify.slack_webhook == "https://hooks.test/slack"
    assert config.new_models[0].model_glob == "claude-sonnet-*"


def test_explicitly_empty_rules_mean_no_rules() -> None:
    assert Config.model_validate({"rules": []}).policy.rules == ()


def test_database_path_can_be_overridden_by_the_environment(tmp_path: Path) -> None:
    config = load_config(None, env={"SUNDIAL_DB": str(tmp_path / "env.db")})

    assert config.db == tmp_path / "env.db"


def test_unknown_config_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("ruels: []\n", encoding="utf-8")

    with pytest.raises(ValueError, match="ruels"):
        load_config(path, env={})


def test_deprecation_event_names_model_date_replacement_and_source() -> None:
    after = rec(state=LifecycleState.DEPRECATED, retire_on=date(2026, 10, 1), replacements=("gpt-y",))

    text = render_event(event(EventType.DEPRECATION_ANNOUNCED, after))

    assert "[openai] gpt-x" in text
    assert "2026-10-01" in text
    assert "gpt-y" in text
    assert "https://docs.test/page" in text


def test_moved_date_event_shows_both_dates() -> None:
    before = rec(state=LifecycleState.DEPRECATED, retire_on=date(2026, 10, 1))
    after = rec(state=LifecycleState.DEPRECATED, retire_on=date(2026, 11, 1))

    text = render_event(event(EventType.RETIREMENT_DATE_CHANGED, after, before))

    assert "2026-10-01" in text and "2026-11-01" in text


def test_azure_events_include_the_version() -> None:
    after = rec("azure", "gpt-4o", version="2024-05-13", state=LifecycleState.DEPRECATED)

    assert "gpt-4o (2024-05-13)" in render_event(event(EventType.DEPRECATION_ANNOUNCED, after))


def test_new_model_matching_a_rule_renders_a_ready_litellm_snippet() -> None:
    rules = (NewModelRule(name="claude", platform="anthropic", model_glob="claude-sonnet-*"),)

    proposal = render_proposal(event(EventType.MODEL_ADDED, rec("anthropic", "claude-sonnet-6")), rules)

    assert proposal is not None
    assert "model_name: claude-sonnet-6" in proposal
    assert "model: anthropic/claude-sonnet-6" in proposal
    assert "claude" in proposal


def test_azure_proposal_asks_for_a_deployment_name_and_sets_base_model() -> None:
    rules = (NewModelRule(name="any-azure", platform="azure"),)

    proposal = render_proposal(event(EventType.MODEL_ADDED, rec("azure", "gpt-6")), rules)

    assert proposal is not None
    assert "model: azure/<your-deployment-name>" in proposal
    assert "base_model: azure/gpt-6" in proposal


def test_new_model_outside_every_rule_and_other_event_types_get_no_proposal() -> None:
    rules = (NewModelRule(name="claude", platform="anthropic", model_glob="claude-sonnet-*"),)

    assert render_proposal(event(EventType.MODEL_ADDED, rec("anthropic", "claude-haiku-9")), rules) is None
    assert render_proposal(event(EventType.MODEL_RETIRED, rec("anthropic", "claude-sonnet-6")), rules) is None


def test_notifications_post_slack_shaped_json_and_include_proposals() -> None:
    posted: list[tuple[str, dict]] = []
    config = Config.model_validate(
        {
            "notify": {"slack_webhook": "https://hooks.test/slack", "webhook": "https://hooks.test/generic"},
            "new_models": [{"name": "all-openai", "platform": "openai"}],
        }
    )

    errors = notify_events(
        [event(EventType.MODEL_ADDED, rec())], config, lambda url, body: posted.append((url, body))
    )

    assert errors == []
    assert [url for url, _ in posted] == ["https://hooks.test/slack", "https://hooks.test/generic"]
    assert "model_name: gpt-x" in posted[0][1]["text"]
    assert posted[1][1]["source"] == "sundial"


def test_failing_webhook_is_reported_not_raised() -> None:
    config = Config.model_validate({"notify": {"slack_webhook": "https://hooks.test/slack"}})

    def broken(url: str, body: dict) -> None:
        raise OSError("connection refused")

    errors = notify_events([event(EventType.MODEL_ADDED, rec())], config, broken)

    assert len(errors) == 1 and "connection refused" in errors[0]


def test_no_targets_means_nothing_is_posted() -> None:
    posted: list[object] = []

    assert (
        notify_events([event(EventType.MODEL_ADDED, rec())], Config(), lambda url, body: posted.append(url))
        == []
    )
    assert posted == []
