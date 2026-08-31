"""The `agon` command line — audit, plan, apply, baseline, verify, debate.

Read-only by construction: `plan` computes and prints but writes nothing;
`apply` without ``--confirm-write`` prints the plan and exits 0 having
dispatched nothing; ``AGON_READ_ONLY=1`` overrides every flag.

The connection options (``--config``, ``--adapter``, ``--fixtures``,
``--confirm-write``) are accepted at BOTH group and subcommand level — the
invocations printed in docs/writes.md §5 and the README quickstart put them
after the subcommand (``agon apply --config account.yaml --confirm-write``),
and a subcommand value wins when both levels supply one.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import click

from agon.adapters.base import EntitySnapshot
from agon.adapters.fixture import FixtureAdapter
from agon.baselines import compute_baselines
from agon.config import Config, ConfigError, load_config
from agon.council import (
    AGENT_ROSTER,
    brief,
    build_debate_context,
    charter_block,
    contested,
)
from agon.pipeline import Pipeline, RunResult
from agon.report import render_report
from agon.writes import dispatch, previous_run_state, read_only_env

logger = logging.getLogger(__name__)

# Exit code for a run whose guards or circuit breakers tripped: no writes
# happened, and cron must be able to tell that from a healthy run.
GUARD_EXIT_CODE = 2

# .env.example documents META_GRAPH_VERSION; the adapter default matches.
ENV_GRAPH_VERSION = "META_GRAPH_VERSION"


def _force_utf8_stdio() -> None:
    """The report carries Δ / § / × — a console in a legacy codepage
    (Windows cp1252) must not turn the house voice into a UnicodeEncodeError.
    Re-encode both streams as UTF-8 where the runtime allows it."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8")


def _build_adapter(meta: bool, fixtures: str | None, config: Config):
    if not meta:
        if not fixtures:
            fallback = Path("tests/fixtures")
            if fallback.exists():
                # Developer convenience: the bundled demo fixtures, so the
                # documented read-only commands run from a fresh checkout.
                fixtures = str(fallback)
            else:
                raise click.UsageError(
                    "--fixtures PATH is required with --adapter fixture"
                )
        return FixtureAdapter(fixtures)
    # Imported lazily so fixture-only environments never import requests paths.
    from agon.adapters.meta import DEFAULT_GRAPH_VERSION, MetaAdapter

    graph_version = os.environ.get(ENV_GRAPH_VERSION) or DEFAULT_GRAPH_VERSION
    return MetaAdapter(
        allowed_account_ids=config.account.allowed_account_ids,
        graph_version=graph_version,
    )


@click.group()
@click.option("--config", "config_path", default="examples/config.example.yaml",
              show_default=True, help="Path to the account YAML config.")
@click.option("--adapter", "adapter_name", type=click.Choice(["meta", "fixture"]),
              default="fixture", show_default=True, help="Platform backend.")
@click.option("--fixtures", "fixtures", default=None, type=click.Path(),
              help="Fixture directory for --adapter fixture.")
@click.option("--dry-run/--confirm-write", "dry_run", default=True,
              help="Default is dry-run; --confirm-write is required to dispatch.")
@click.pass_context
def main(
    ctx: click.Context,
    config_path: str,
    adapter_name: str,
    fixtures: str | None,
    dry_run: bool,
) -> None:
    """Agon — adversarial autopilot for Meta Ads accounts."""
    _force_utf8_stdio()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    ctx.ensure_object(dict)
    ctx.obj.update(
        config=config,
        adapter=_build_adapter(adapter_name == "meta", fixtures, config),
        adapter_is_meta=(adapter_name == "meta"),
        fixtures=fixtures,
        confirm_write=not dry_run,
        audit_path=config.reporting.audit_log,
    )


def _common_options(command):
    """The connection options, also accepted on every subcommand.

    docs/writes.md §5 and the README quickstart print them there; a
    subcommand value overrides the group's when both are given.
    """
    decorators = (
        click.option("--config", "config_path", default=None, type=click.Path(),
                     help="Account YAML config. Overrides the group value."),
        click.option("--adapter", "adapter_name", default=None,
                     type=click.Choice(["meta", "fixture"]),
                     help="Platform backend. Overrides the group value."),
        click.option("--fixtures", "fixtures", default=None, type=click.Path(),
                     help="Fixture directory for --adapter fixture. Overrides "
                          "the group value."),
        click.option("--dry-run/--confirm-write", "dry_run", default=None,
                     help="Default is dry-run; --confirm-write dispatches. "
                          "Overrides the group value."),
    )
    for decorator in decorators:
        command = decorator(command)
    return command


def _apply_overrides(
    ctx: click.Context,
    config_path: Optional[str],
    adapter_name: Optional[str],
    fixtures: Optional[str],
    dry_run: Optional[bool],
) -> None:
    """Subcommand-level options win over the group's (docs/writes.md §5)."""
    obj = ctx.obj
    if config_path is not None:
        try:
            obj["config"] = load_config(config_path)
        except ConfigError as exc:
            raise click.ClickException(str(exc)) from exc
        obj["audit_path"] = obj["config"].reporting.audit_log
    if config_path is not None or adapter_name is not None or fixtures is not None:
        meta = (
            (adapter_name == "meta")
            if adapter_name is not None
            else obj.get("adapter_is_meta", False)
        )
        fixture_path = fixtures if fixtures is not None else obj.get("fixtures")
        obj["adapter"] = _build_adapter(meta, fixture_path, obj["config"])
        obj["adapter_is_meta"] = meta
        obj["fixtures"] = fixture_path
    if dry_run is not None:
        obj["confirm_write"] = not dry_run


def _run(ctx: click.Context) -> RunResult:
    return Pipeline(ctx.obj["adapter"], ctx.obj["config"]).run()


def _guard_tripped(result) -> bool:
    return result.guard.urgent or not result.guard.writes_allowed


@main.command()
@_common_options
@click.pass_context
def audit(
    ctx: click.Context,
    config_path: Optional[str],
    adapter_name: Optional[str],
    fixtures: Optional[str],
    dry_run: Optional[bool],
) -> None:
    """Read-only snapshot: entities, baselines, guard state. Writes nothing."""
    _apply_overrides(ctx, config_path, adapter_name, fixtures, dry_run)
    result = _run(ctx)
    previous = previous_run_state(ctx.obj["audit_path"])
    click.echo(render_report(result, dispatch=None, previous=previous))


@main.command()
@_common_options
@click.pass_context
def plan(
    ctx: click.Context,
    config_path: Optional[str],
    adapter_name: Optional[str],
    fixtures: Optional[str],
    dry_run: Optional[bool],
) -> None:
    """Compute the action set and print the report. Writes nothing."""
    _apply_overrides(ctx, config_path, adapter_name, fixtures, dry_run)
    result = _run(ctx)
    previous = previous_run_state(ctx.obj["audit_path"])
    click.echo(render_report(result, dispatch=None, previous=previous))
    click.echo("")
    click.echo(
        "plan only — nothing dispatched. Use `agon apply --confirm-write` to dispatch."
    )


@main.command()
@_common_options
@click.pass_context
def apply(
    ctx: click.Context,
    config_path: Optional[str],
    adapter_name: Optional[str],
    fixtures: Optional[str],
    dry_run: Optional[bool],
) -> None:
    """Dispatch the computed action set. Requires --confirm-write."""
    _apply_overrides(ctx, config_path, adapter_name, fixtures, dry_run)
    result = _run(ctx)
    confirm_write = ctx.obj["confirm_write"]
    # The §8 delta must compare against the PREVIOUS run — read it before
    # dispatch appends this run's entries to the same audit log.
    previous = previous_run_state(ctx.obj["audit_path"])
    everything = result.actions + result.proposals
    dispatch_result = dispatch(
        everything,
        ctx.obj["adapter"],
        result.config,
        result.guard,
        confirm_write=confirm_write,
        audit_path=ctx.obj["audit_path"],
        daily_spend=result.daily_spend,
    )
    click.echo(render_report(result, dispatch=dispatch_result, previous=previous))
    if _guard_tripped(result):
        # No writes happened; cron must be able to tell this from a healthy
        # run (docs/gates.md §10 — report-only, flag the gap).
        click.echo("")
        click.echo(
            "GUARD TRIP: a guard or circuit breaker tripped — no writes this "
            "run. Exiting 2 so schedulers can detect it."
        )
        ctx.exit(GUARD_EXIT_CODE)
    if not confirm_write:
        click.echo("")
        click.echo(
            "apply without --confirm-write is a dry run: nothing dispatched, exit 0."
        )
        return
    if read_only_env():
        click.echo("")
        click.echo("AGON_READ_ONLY is set — propose-only regardless of --confirm-write.")
        return
    if dispatch_result.dispatched_count:
        click.echo("")
        click.echo(f"dispatched {dispatch_result.dispatched_count} action(s).")


@main.command()
@_common_options
@click.pass_context
def baseline(
    ctx: click.Context,
    config_path: Optional[str],
    adapter_name: Optional[str],
    fixtures: Optional[str],
    dry_run: Optional[bool],
) -> None:
    """Per-market baselines: value, source, population (§3)."""
    _apply_overrides(ctx, config_path, adapter_name, fixtures, dry_run)
    adapter = ctx.obj["adapter"]
    config = ctx.obj["config"]
    snapshot: EntitySnapshot = adapter.fetch_entities()
    for computed in compute_baselines(snapshot.adsets, config).values():
        click.echo(computed.describe())


@main.command()
@_common_options
@click.argument("source_ad_id")
@click.argument("copy_ad_id")
@click.pass_context
def verify(
    ctx: click.Context,
    config_path: Optional[str],
    adapter_name: Optional[str],
    fixtures: Optional[str],
    dry_run: Optional[bool],
    source_ad_id: str,
    copy_ad_id: str,
) -> None:
    """Confirm a duplicate kept its post ID (framework.md §4)."""
    _apply_overrides(ctx, config_path, adapter_name, fixtures, dry_run)
    adapter = ctx.obj["adapter"]
    source = adapter.get_ad(source_ad_id)
    copy = adapter.get_ad(copy_ad_id)
    if source.post_id and source.post_id == copy.post_id:
        click.echo(
            f"OK: ad {copy_ad_id} carries post {copy.post_id}, same as source "
            f"{source_ad_id}."
        )
        return
    raise click.ClickException(
        f"VERIFY FAILED: ad {copy_ad_id} carries post {copy.post_id!r}, source "
        f"{source_ad_id} carries {source.post_id!r} — the duplicate did NOT "
        "keep its post ID"
    )


@main.command()
@_common_options
@click.pass_context
def debate(
    ctx: click.Context,
    config_path: Optional[str],
    adapter_name: Optional[str],
    fixtures: Optional[str],
    dry_run: Optional[bool],
) -> None:
    """Print the contested-action briefs and roster charters for the council.

    docs/debate-protocol.md §4: the Python layer prepares and enforces but
    never calls a model — this prints each contested action's Round 0 brief
    WITH its numbers (built from the run state) plus the full charters, so
    the output can be fed to any agent runtime.
    """
    _apply_overrides(ctx, config_path, adapter_name, fixtures, dry_run)
    result = _run(ctx)
    contested_actions = contested(result.actions + result.proposals)
    if not contested_actions:
        click.echo("None this run — no action drew opposition from the council.")
        return
    for item in contested_actions:
        context = build_debate_context(
            item.action,
            baselines=result.baselines,
            campaigns=result.campaigns,
            adsets=result.adsets,
            ads=result.ads,
            config=result.config,
        )
        click.echo(brief(item.action, context))
        click.echo("")
    click.echo("## Round 1 — the roster, one charter per councillor")
    click.echo("")
    for archetype in AGENT_ROSTER:
        click.echo(charter_block(archetype))
        click.echo("")
    click.echo(
        "Each councillor receives exactly one brief above plus its own "
        "charter, and returns position / argument / pre-emptive strike; the "
        "ruling frame (RULING / AGAINST / BASIS / FLIP) closes the round "
        "(docs/debate-protocol.md §2)."
    )


if __name__ == "__main__":
    main(prog_name="agon")
