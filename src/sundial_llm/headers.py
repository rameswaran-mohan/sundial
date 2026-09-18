"""Response headers for a decision: RFC 9745 Deprecation, RFC 8594 Sunset, plus Sundial details."""

from __future__ import annotations

from datetime import date, datetime, timezone
from email.utils import format_datetime

from sundial_llm.policy import Action, Decision


def _midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def decision_headers(decision: Decision) -> dict[str, str]:
    record = decision.record
    if decision.action is Action.ALLOW or record is None:
        return {}
    headers: dict[str, str] = {}
    deprecated_on = record.deprecated_on or record.announced_on
    if deprecated_on is not None:
        headers["Deprecation"] = f"@{int(_midnight_utc(deprecated_on).timestamp())}"
    if record.retire_on is not None and not record.retire_on_is_floor:
        headers["Sunset"] = format_datetime(_midnight_utc(record.retire_on), usegmt=True)
    if decision.state is not None:
        headers["X-Sundial-State"] = decision.state.value
    successor = decision.target or ", ".join(record.replacements)
    if successor:
        headers["X-Sundial-Successor"] = successor
    if decision.rule:
        headers["X-Sundial-Rule"] = decision.rule
    if decision.days_left is not None:
        headers["X-Sundial-Days-Left"] = str(decision.days_left)
    if decision.approximate:
        headers["X-Sundial-Approximate"] = "true"
    return headers


REDIRECTED_FROM = "X-Sundial-Redirected-From"


def block_message(decision: Decision, public_model: str) -> str:
    """The error text a client sees when a request is refused."""
    record = decision.record
    if record is None:
        return f"Model '{public_model}' is blocked by Sundial."
    retire_on = record.retire_on.isoformat() if record.retire_on else None
    if decision.state is not None and decision.state.value == "retired":
        status = f"was retired on {retire_on}" if retire_on else "is retired"
    else:
        state = decision.state.value if decision.state else record.state.value
        status = f"is {state}" + (f" and retires on {retire_on}" if retire_on else "")
    successor = decision.target or ", ".join(record.replacements)
    advice = f" Use {successor} instead." if successor else ""
    rule = f" (Sundial rule '{decision.rule}')" if decision.rule else ""
    return f"Model '{public_model}' {status}{rule}.{advice}"
