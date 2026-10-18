from datetime import date, datetime, timedelta, timezone

from sundial_llm.guard import IdentityResolver, LifecycleGuard
from sundial_llm.matching import UpstreamModel
from sundial_llm.models import LifecycleRecord, LifecycleState
from sundial_llm.policy import Action, load_policy

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


def test_a_failing_record_load_never_breaks_the_request() -> None:
    def load() -> dict:
        raise OSError("db locked")

    subject = LifecycleGuard(load, IdentityResolver(), BLOCK_RETIRED, clock=Clock())

    assert subject.decide("gpt-dead").action is Action.ALLOW
