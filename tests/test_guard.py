import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from sundial_llm.guard import IdentityResolver, LifecycleGuard
from sundial_llm.matching import UpstreamModel
from sundial_llm.models import LifecycleRecord, LifecycleState
from sundial_llm.policy import ALLOW, Action, load_policy

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)

BLOCK_RETIRED = load_policy(
    {"rules": [{"name": "block-retired", "when": {"state": "retired"}, "action": "block", "mode": "apply"}]}
)


def rec(model_id: str, **extra: object) -> LifecycleRecord:
    base = {"platform": "openai", "state": LifecycleState.DEPRECATED, "source_url": "u", "evidence": "e"}
    return LifecycleRecord(model_id=model_id, **{**base, **extra})


RETIRED = rec("gpt-dead", retire_on=date(2026, 1, 1))
LIVE = rec("gpt-live", state=LifecycleState.ACTIVE)


def index(*records: LifecycleRecord) -> dict:
    return {r.key: r for r in records}


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class GroupResolver:
    def __init__(self, groups: dict[str, list[str]]) -> None:
        self.groups = groups

    def resolve(self, public_model: str) -> list[UpstreamModel]:
        return [UpstreamModel("openai", model) for model in self.groups.get(public_model, [])]


def guard(records: dict, resolver=None, **kwargs) -> LifecycleGuard:
    return LifecycleGuard(
        lambda: records, resolver or IdentityResolver(), BLOCK_RETIRED, clock=Clock(), **kwargs
    )


def test_unknown_model_is_allowed() -> None:
    assert guard(index(RETIRED)).decide("gpt-something-else").action is Action.ALLOW


def test_retired_model_is_blocked_through_the_identity_resolver() -> None:
    decision = guard(index(RETIRED)).decide("openai/gpt-dead")

    assert (decision.action, decision.enforced) == (Action.BLOCK, True)


def test_group_is_blocked_only_when_every_deployment_is_blocked() -> None:
    resolver = GroupResolver({"all-dead": ["gpt-dead", "gpt-dead"], "half-dead": ["gpt-dead", "gpt-live"]})
    subject = guard(index(RETIRED, LIVE), resolver)

    assert subject.decide("all-dead").enforced is True
    half = subject.decide("half-dead")
    assert half.action is Action.BLOCK
    assert half.enforced is False


def test_healthy_group_reports_its_tracked_deployment_whatever_the_order() -> None:
    resolver = GroupResolver(
        {"unknown-first": ["gpt-unknown", "gpt-live"], "tracked-first": ["gpt-live", "gpt-unknown"]}
    )
    subject = guard(index(RETIRED, LIVE), resolver)

    for group in ("unknown-first", "tracked-first"):
        decision = subject.decide(group)
        assert decision.action is Action.ALLOW
        assert decision.key == LIVE.key


SOON = rec("gpt-soon", state=LifecycleState.ACTIVE, retire_on=date(2027, 1, 1))
LATER = rec("gpt-later", state=LifecycleState.ACTIVE, retire_on=date(2028, 1, 1))


def test_healthy_group_reports_the_deployment_retiring_soonest_whatever_the_order() -> None:
    resolver = GroupResolver(
        {
            "soon-first": ["gpt-soon", "gpt-later"],
            "later-first": ["gpt-later", "gpt-soon"],
            "undated-first": ["gpt-unknown", "gpt-live", "gpt-later", "gpt-soon"],
            "undated-last": ["gpt-later", "gpt-soon", "gpt-live", "gpt-unknown"],
        }
    )
    subject = guard(index(RETIRED, LIVE, SOON, LATER), resolver)

    for group in resolver.groups:
        decision = subject.decide(group)
        assert decision.action is Action.ALLOW
        assert decision.key == SOON.key, group


def test_group_with_no_deployments_is_allowed() -> None:
    assert guard(index(RETIRED), GroupResolver({})).decide("ghost").action is Action.ALLOW


def test_records_are_reloaded_only_after_the_ttl() -> None:
    loads: list[int] = []
    current = {"records": index()}

    def load() -> dict:
        loads.append(1)
        return current["records"]

    clock = Clock()
    subject = LifecycleGuard(load, IdentityResolver(), BLOCK_RETIRED, clock=clock, reload_seconds=300)

    assert subject.decide("gpt-dead").action is Action.ALLOW
    current["records"] = index(RETIRED)
    clock.now = NOW + timedelta(seconds=299)
    assert subject.decide("gpt-dead").action is Action.ALLOW
    clock.now = NOW + timedelta(seconds=301)
    assert subject.decide("gpt-dead").action is Action.BLOCK
    assert len(loads) == 2


def test_a_failing_resolver_never_breaks_the_request() -> None:
    class Broken:
        def resolve(self, public_model: str) -> list[UpstreamModel]:
            raise RuntimeError("router not ready")

    assert guard(index(RETIRED), Broken()).decide("gpt-dead").action is Action.ALLOW


def test_a_failed_check_logs_the_client_model_quoted_and_cut_to_200_characters(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Broken:
        def resolve(self, public_model: str) -> list[UpstreamModel]:
            raise RuntimeError("router not ready")

    model = "gpt-x\nERROR sundial: forged line " + "m" * 300

    assert guard(index(RETIRED), Broken()).decide(model) == ALLOW
    assert caplog.messages == [
        f"sundial: lifecycle check failed for {repr(model)[:200]}; allowing the request"
    ]


def test_a_failing_record_load_never_breaks_the_request() -> None:
    def load() -> dict:
        raise OSError("db locked")

    subject = LifecycleGuard(load, IdentityResolver(), BLOCK_RETIRED, clock=Clock())

    assert subject.decide("gpt-dead").action is Action.ALLOW


class RaisingResolver:
    def resolve(self, public_model: str) -> list[UpstreamModel]:
        raise AssertionError("the resolver must not be called when upstreams are given")


def test_explicit_upstreams_are_decided_without_calling_the_resolver() -> None:
    subject = guard(index(RETIRED, LIVE), RaisingResolver())

    decision = subject.decide("whatever-the-client-sent", upstreams=(UpstreamModel("openai", "gpt-dead"),))

    assert (decision.action, decision.enforced, decision.key) == (Action.BLOCK, True, RETIRED.key)


def test_explicit_upstreams_follow_the_group_rule() -> None:
    subject = guard(index(RETIRED, LIVE), RaisingResolver())

    decision = subject.decide(
        "mixed", upstreams=(UpstreamModel("openai", "gpt-dead"), UpstreamModel("openai", "gpt-live"))
    )

    assert (decision.action, decision.enforced) == (Action.BLOCK, False)


def test_an_empty_upstream_sequence_allows_without_resolving() -> None:
    # The identity resolver would read "gpt-dead" as the retired OpenAI model and block it.
    decision = guard(index(RETIRED)).decide("gpt-dead", upstreams=())

    assert decision == ALLOW


LEAVING = rec("gpt-leaving", retire_on=date(2026, 9, 22))
REDIRECT_WEEK = load_policy(
    {
        "rules": [
            {
                "name": "redirect-week",
                "when": {"days_until_retirement_lte": 7},
                "action": "redirect",
                "mode": "apply",
            }
        ],
        "successors": {"public-chat": "gpt-next", "gpt-leaving": "gpt-wrong"},
    }
)


def test_the_successor_is_looked_up_by_the_public_model_not_the_upstream() -> None:
    subject = LifecycleGuard(lambda: index(LEAVING), RaisingResolver(), REDIRECT_WEEK, clock=Clock())

    decision = subject.decide("public-chat", upstreams=(UpstreamModel("openai", "gpt-leaving"),))

    assert (decision.action, decision.enforced, decision.target) == (Action.REDIRECT, True, "gpt-next")


def test_a_fresh_guard_loads_its_records_to_count_them() -> None:
    assert guard(index(RETIRED, LIVE)).record_count() == 2


def test_record_count_loads_once_inside_the_ttl() -> None:
    loads: list[int] = []

    def load() -> dict:
        loads.append(1)
        return index(RETIRED, LIVE)

    clock = Clock()
    subject = LifecycleGuard(load, IdentityResolver(), BLOCK_RETIRED, clock=clock, reload_seconds=300)

    assert subject.record_count() == 2
    clock.now = NOW + timedelta(seconds=299)
    assert subject.record_count() == 2
    assert subject.decide("gpt-dead").action is Action.BLOCK
    assert len(loads) == 1


def test_record_count_never_raises_when_the_load_fails() -> None:
    def load() -> dict:
        raise OSError("db locked")

    subject = LifecycleGuard(load, IdentityResolver(), BLOCK_RETIRED, clock=Clock())

    assert subject.record_count() == 0


def test_record_count_keeps_the_records_it_holds_when_a_reload_fails() -> None:
    current: dict = {"load": lambda: index(RETIRED, LIVE)}

    def load() -> dict:
        return current["load"]()

    def broken() -> dict:
        raise OSError("db locked")

    clock = Clock()
    subject = LifecycleGuard(load, IdentityResolver(), BLOCK_RETIRED, clock=clock, reload_seconds=300)

    assert subject.record_count() == 2
    current["load"] = broken
    clock.now = NOW + timedelta(seconds=301)
    assert subject.record_count() == 2


class FlakyLoader:
    """Loads `records`, or raises while `failing` is set, and counts every call."""

    def __init__(self, records: dict, failing: bool = False) -> None:
        self.records = records
        self.failing = failing
        self.calls = 0

    def __call__(self) -> dict:
        self.calls += 1
        if self.failing:
            raise sqlite3.OperationalError("database is locked")
        return self.records


LOAD_FAILED = "sundial: could not load lifecycle records"


def test_held_records_keep_enforcing_after_a_failed_reload(caplog: pytest.LogCaptureFixture) -> None:
    load = FlakyLoader(index(RETIRED))
    clock = Clock()
    subject = LifecycleGuard(load, IdentityResolver(), BLOCK_RETIRED, clock=clock, reload_seconds=300)
    assert subject.decide("openai/gpt-dead").action is Action.BLOCK

    load.failing = True
    clock.now = NOW + timedelta(seconds=301)
    decision = subject.decide("openai/gpt-dead")

    assert (decision.action, decision.enforced) == (Action.BLOCK, True)
    assert subject.record_count() == 1
    assert load.calls == 2
    failures = [r for r in caplog.records if r.getMessage().startswith(LOAD_FAILED)]
    assert [(r.levelno, r.exc_info is not None) for r in failures] == [(logging.WARNING, True)]


@pytest.mark.parametrize(("reload_seconds", "backoff"), [(300, 30), (10, 10)], ids=["capped", "short-ttl"])
def test_a_failed_load_is_not_retried_inside_the_backoff(reload_seconds: float, backoff: int) -> None:
    load = FlakyLoader(index(RETIRED), failing=True)
    clock = Clock()
    subject = LifecycleGuard(
        load, IdentityResolver(), BLOCK_RETIRED, clock=clock, reload_seconds=reload_seconds
    )

    assert subject.decide("openai/gpt-dead") == ALLOW  # nothing is held yet, so it still allows
    clock.now = NOW + timedelta(seconds=backoff - 1)
    assert subject.decide("openai/gpt-dead") == ALLOW
    assert subject.record_count() == 0
    assert load.calls == 1

    clock.now = NOW + timedelta(seconds=backoff)
    assert subject.decide("openai/gpt-dead") == ALLOW  # retried, and it fails again
    assert load.calls == 2
    clock.now = NOW + timedelta(seconds=2 * backoff - 1)
    subject.decide("openai/gpt-dead")
    assert load.calls == 2

    load.failing = False
    clock.now = NOW + timedelta(seconds=2 * backoff)
    assert subject.decide("openai/gpt-dead").action is Action.BLOCK
    assert load.calls == 3
    clock.now = NOW + timedelta(seconds=2 * backoff + reload_seconds)
    assert subject.record_count() == 1
    assert load.calls == 3  # back on the reload TTL once a load succeeds


def test_a_failing_record_load_with_explicit_upstreams_never_breaks_the_request() -> None:
    def load() -> dict:
        raise OSError("db locked")

    subject = LifecycleGuard(load, RaisingResolver(), BLOCK_RETIRED, clock=Clock())

    assert subject.decide("gpt-dead", upstreams=(UpstreamModel("openai", "gpt-dead"),)) == ALLOW
