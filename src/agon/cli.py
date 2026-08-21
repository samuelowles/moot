"""The `agon` command line — audit, plan, apply, baseline, verify, debate.

Read-only by construction: `plan` computes and prints but writes nothing;
`apply` without ``--confirm-write`` prints the plan and exits 0 having
dispatched nothing; ``AGON_READ_ONLY=1`` overrides every flag.
"""

from __future__ import annotations

import logging

import click

from agon.adapters.base import EntitySnapshot
from agon.adapters.fixture import FixtureAdapter
from agon.baselines import compute_baselines
from agon.config import ConfigError, load_config
from agon.council import brief, contested
from agon.pipeline import Pipeline
from agon.report import render_report
from agon.writes import dispatch, previous_run_state, read_only_env

logger = logging.getLogger(__name__)


def _build_adapter(meta: bool, fixtures: str | None, config):
    if not meta:
        if not fixtures:
            raise click.UsageError(
                "--fixtures PATH is required with --adapter fixture"
            )
        return FixtureAdapter(fixtures)
    # Imported lazily so fixture-only environments never import requests paths.
    from agon.adapters.meta import MetaAdapter

    return MetaAdapter(allowed_account_ids=config.account.allowed_account_ids)


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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    ctx.ensure_object(dict)
    ctx.obj.update(
        config=config,
        adapter=_build_adapter(adapter_name == "meta", fixtures, config),
        confirm_write=not dry_run,
        audit_path=config.reporting.audit_log,
    )


def _run(ctx: click.Context):
    return Pipeline(ctx.obj["adapter"], ctx.obj["config"]).run()


@main.command()
@click.pass_context
def audit(ctx: click.Context) -> None:
    """Read-only snapshot: entities, baselines, guard state. Writes nothing."""
    result = _run(ctx)
    previous = previous_run_state(ctx.obj["audit_path"])
    click.echo(render_report(result, dispatch=None, previous=previous))


@main.command()
@click.pass_context
def plan(ctx: click.Context) -> None:
    """Compute the action set and print the report. Writes nothing."""
    result = _run(ctx)
    previous = previous_run_state(ctx.obj["audit_path"])
    click.echo(render_report(result, dispatch=None, previous=previous))
    click.echo("")
    click.echo(
        "plan only — nothing dispatched. Use `agon apply --confirm-write` to dispatch."
    )


@main.command()
@click.pass_context
def apply(ctx: click.Context) -> None:
    """Dispatch the computed action set. Requires --confirm-write."""
    result = _run(ctx)
    confirm_write = ctx.obj["confirm_write"]
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
    previous = previous_run_state(ctx.obj["audit_path"])
    click.echo(render_report(result, dispatch=dispatch_result, previous=previous))
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
@click.pass_context
def baseline(ctx: click.Context) -> None:
    """Per-market baselines: value, source, population (§3)."""
    adapter = ctx.obj["adapter"]
    config = ctx.obj["config"]
    snapshot: EntitySnapshot = adapter.fetch_entities()
    for computed in compute_baselines(snapshot.adsets, config).values():
        click.echo(computed.describe())


@main.command()
@click.argument("source_ad_id")
@click.argument("copy_ad_id")
@click.pass_context
def verify(ctx: click.Context, source_ad_id: str, copy_ad_id: str) -> None:
    """Confirm a duplicate kept its post ID (framework.md §4)."""
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
@click.pass_context
def debate(ctx: click.Context) -> None:
    """Print the contested-action briefs for the council to argue."""
    result = _run(ctx)
    actions = result.actions + result.proposals
    contested_actions = contested(actions)
    if not contested_actions:
        click.echo("None this run — no action drew opposition from the council.")
        return
    for item in contested_actions:
        click.echo(brief(item.action))
        click.echo("")


if __name__ == "__main__":
    main(prog_name="agon")
