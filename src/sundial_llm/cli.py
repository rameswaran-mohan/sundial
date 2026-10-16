"""Command line: scan, import, export, status, check, events, costmap-patch."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from sundial_llm import __version__
from sundial_llm.config import Config, load_config
from sundial_llm.costmap import build_patch
from sundial_llm.guard import IdentityResolver, LifecycleGuard
from sundial_llm.matching import UpstreamModel, parse_upstream
from sundial_llm.notify import Post, http_post, notify_events, render_event
from sundial_llm.policy import Action
from sundial_llm.registry import build_document, parse_document, write_if_changed
from sundial_llm.scan import ScanOutcome, run_import, run_scan
from sundial_llm.sources import ALL_SOURCES, Fetch, http_fetch
from sundial_llm.store import Store

EXIT_OK = 0
EXIT_ALL_SOURCES_FAILED = 1
EXIT_BAD_INPUT = 2
EXIT_MODEL_FLAGGED = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sundial", description="Model lifecycle guard for LLM gateways.")
    parser.add_argument("--version", action="version", version=f"sundial-llm {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="path to a sundial YAML config (default: $SUNDIAL_CONFIG)")
    common.add_argument("--db", help="path to the registry database (overrides the config)")
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser(
        "scan", parents=[common], help="fetch every provider source and record changes"
    )
    scan.add_argument("--strict", action="store_true", help="exit 1 if any source failed, not only all")

    importer = commands.add_parser("import", parents=[common], help="load a published registry document")
    importer.add_argument("source", help="path or http(s) URL of a registry JSON document")

    export = commands.add_parser("export", parents=[common], help="write the registry as a JSON document")
    export.add_argument("--out", required=True, metavar="FILE")

    status = commands.add_parser("status", parents=[common], help="list upcoming retirements")
    status.add_argument("--within", type=int, default=90, metavar="DAYS")
    status.add_argument("--platform")

    check = commands.add_parser("check", parents=[common], help="evaluate one model against the policy")
    check.add_argument("model", help="gateway model string, for example azure/my-deployment")
    check.add_argument("--base-model", help="underlying model for deployments with arbitrary names")

    events = commands.add_parser("events", parents=[common], help="show recorded lifecycle events")
    events.add_argument("--limit", type=int, default=20)

    costmap = commands.add_parser(
        "costmap-patch", parents=[common], help="compare a litellm cost map with provider dates"
    )
    costmap.add_argument("--cost-map", required=True, metavar="PATH")
    costmap.add_argument("--out", metavar="FILE", help="write the patch mapping as JSON")
    return parser


def _config(args: argparse.Namespace, env: Mapping[str, str]) -> Config:
    config = load_config(args.config, env=env)
    return config.model_copy(update={"db": Path(args.db)}) if args.db else config


def _scan(config: Config, strict: bool, fetch: Fetch, now: datetime, post: Post, out: TextIO) -> int:
    outcome = run_scan(Store(config.db), ALL_SOURCES, fetch, now)
    _report(outcome, config, post, out)
    if not outcome.sources_ok or (strict and outcome.failures):
        return EXIT_ALL_SOURCES_FAILED
    return EXIT_OK


def _read_source(source: str, fetch: Fetch) -> str:
    if source.startswith(("http://", "https://")):
        return fetch(source)
    return Path(source).read_text(encoding="utf-8")


def _import(config: Config, source: str, fetch: Fetch, now: datetime, post: Post, out: TextIO) -> int:
    try:
        text = _read_source(source, fetch)
    except Exception as error:  # noqa: BLE001 - an unreachable source is reported like a failed scan
        print(f"sundial: cannot read {source}: {error}", file=out)
        return EXIT_ALL_SOURCES_FAILED
    try:
        document = parse_document(text)
    except ValueError as error:
        print(f"sundial: {source} is not a registry document: {error}", file=out)
        return EXIT_BAD_INPUT
    _report(run_import(Store(config.db), document, now), config, post, out)
    return EXIT_OK


def _export(config: Config, out_path: str, now: datetime, out: TextIO) -> int:
    store = Store(config.db)
    sources = store.last_scan_sources()
    ok = [name for name, outcome in sources.items() if outcome == "ok"]
    failures = {name: outcome for name, outcome in sources.items() if outcome != "ok"}
    document = build_document(store, now, ok, failures)
    count = len(document.records)
    if write_if_changed(document, Path(out_path)):
        print(f"Registry written to {out_path} ({count} records)", file=out)
    else:
        print(f"Registry unchanged ({count} records)", file=out)
    return EXIT_OK


def _report(outcome: ScanOutcome, config: Config, post: Post, out: TextIO) -> None:
    """Print what a scan or import found and send its notifications."""
    ok, failed = len(outcome.sources_ok), len(outcome.failures)
    total = ok + failed
    print(
        f"Scanned {total} source{'' if total == 1 else 's'}: {ok} ok, {failed} failed. "
        f"{outcome.total_records} models tracked.",
        file=out,
    )
    if outcome.baseline and ok:
        print("Baseline created. Later scans report what changed.", file=out)
    elif ok:
        count = len(outcome.events)
        print(f"{count} change{'' if count == 1 else 's'} since the last scan.", file=out)
    for name, reason in outcome.failures.items():
        print(f"  failed   {name}: {reason}", file=out)
    for warning in outcome.warnings:
        print(f"  warning  {warning}", file=out)
    for event in outcome.events:
        print(f"  event    {render_event(event)}", file=out)
    for error in notify_events(outcome.events, config, post):
        print(f"  warning  {error}", file=out)


def _status(config: Config, within: int, platform: str | None, now: datetime, out: TextIO) -> int:
    records = Store(config.db).load_records()
    if not records:
        print("The registry is empty. Run `sundial scan` first.", file=out)
        return EXIT_OK
    today = now.date()
    upcoming = sorted(
        (
            (days, record)
            for record in records.values()
            if (platform is None or record.platform == platform)
            and (days := record.days_until_retirement(today)) is not None
            and 0 < days <= within
        ),
        key=lambda item: (item[0], item[1].key),
    )
    print(f"{len(upcoming)} models retire within {within} days ({len(records)} tracked).", file=out)
    for days, record in upcoming:
        name = f"{record.model_id} ({record.version})" if record.version else record.model_id
        successor = f"  -> {', '.join(record.replacements)}" if record.replacements else ""
        print(f"{days:>5}d  {record.retire_on}  {record.platform:<10} {name}{successor}", file=out)
    return EXIT_OK


class _Fixed:
    def __init__(self, upstream: UpstreamModel) -> None:
        self._upstream = upstream

    def resolve(self, public_model: str) -> Sequence[UpstreamModel]:
        return (self._upstream,)


def _check(config: Config, model: str, base_model: str | None, now: datetime, out: TextIO) -> int:
    store = Store(config.db)
    resolver = _Fixed(parse_upstream(model, base_model)) if base_model else IdentityResolver()
    decision = LifecycleGuard(store.load_records, resolver, config.policy, clock=lambda: now).decide(model)
    record = decision.record
    if decision.action is Action.ALLOW or record is None:
        print(f"{model}: OK, no lifecycle record triggers a rule.", file=out)
        return EXIT_OK
    parts = [f"{model}: {decision.action.value.upper()} (rule {decision.rule}, mode {decision.mode.value})"]
    parts.append(f"state {decision.state.value if decision.state else record.state.value}")
    if record.retire_on:
        days = f" ({decision.days_left} days)" if decision.days_left is not None else ""
        parts.append(f"retires {record.retire_on}{days}")
    if decision.target or record.replacements:
        parts.append(f"replacement {decision.target or ', '.join(record.replacements)}")
    if decision.approximate:
        parts.append("approximate match")
    print(", ".join(parts) + f". Source: {record.source_url}", file=out)
    return EXIT_MODEL_FLAGGED


def _events(config: Config, limit: int, out: TextIO) -> int:
    stored = Store(config.db).events(limit)
    if not stored:
        print("No events recorded yet.", file=out)
    for item in stored:
        print(f"{item.at:%Y-%m-%d %H:%M}  {item.event.type.value:<24} {render_event(item.event)}", file=out)
    return EXIT_OK


def _costmap(config: Config, cost_map_path: str, out_path: str | None, now: datetime, out: TextIO) -> int:
    cost_map = json.loads(Path(cost_map_path).read_text(encoding="utf-8"))
    patch = build_patch(cost_map, Store(config.db).load_records(), now.date())
    print(f"{len(patch.changes)} cost map entries differ from provider-published dates.", file=out)
    for change in patch.changes:
        print(f"  {change.key}: {change.old or 'none'} -> {change.new}  ({change.source_url})", file=out)
    if out_path:
        Path(out_path).write_text(json.dumps(patch.as_mapping(), indent=2) + "\n", encoding="utf-8")
        print(f"Patch written to {out_path}", file=out)
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    fetch: Fetch = http_fetch,
    now: Callable[[], datetime] = _utcnow,
    post: Post = http_post,
    out: TextIO | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    if out is None:
        out = sys.stdout
        if hasattr(out, "reconfigure"):
            out.reconfigure(errors="replace")  # Windows consoles cannot print every provider character
    args = _parser().parse_args(argv)
    try:
        config = _config(args, os.environ if env is None else env)
    except (ValueError, OSError) as error:
        print(f"sundial: {error}", file=out)
        return EXIT_BAD_INPUT
    moment = now()
    if args.command == "scan":
        return _scan(config, args.strict, fetch, moment, post, out)
    if args.command == "import":
        return _import(config, args.source, fetch, moment, post, out)
    if args.command == "export":
        return _export(config, args.out, moment, out)
    if args.command == "status":
        return _status(config, args.within, args.platform, moment, out)
    if args.command == "check":
        return _check(config, args.model, args.base_model, moment, out)
    if args.command == "events":
        return _events(config, args.limit, out)
    return _costmap(config, args.cost_map, args.out, moment, out)


if __name__ == "__main__":
    raise SystemExit(main())
