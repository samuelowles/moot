"""CLI behaviour and report rendering, via click's test runner."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from conftest import CONFIG_PATH, FIXTURES

from agon.adapters.fixture import FixtureAdapter
from agon.cli import main
from agon.report import NONE_THIS_RUN, render_report

ARGS = ["--config", str(CONFIG_PATH), "--adapter", "fixture", "--fixtures", str(FIXTURES)]


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def fresh_adapter(monkeypatch):
    """The CLI builds its own adapter; expose a spy to count writes."""
    created = {}

    real = FixtureAdapter

    class Spy(real):
        def __init__(self, path):
            super().__init__(path)
            created["instance"] = self

    import agon.cli as cli_module

    monkeypatch.setattr(cli_module, "FixtureAdapter", Spy)
    return created


class TestPlan:
    def test_plan_runs_end_to_end(self, runner, fresh_adapter, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # keep the audit log out of the repo
        result = runner.invoke(main, ARGS + ["plan"])
        assert result.exit_code == 0, result.output
        assert "# Agon run report" in result.output
        assert "## Live daily spend by stage" in result.output
        # Plan writes nothing.
        assert fresh_adapter["instance"].writes == []

    def test_plan_prints_every_section_header(self, runner, tmp_path, monkeypatch):
        # A quiet run must be distinguishable from a failed one: sections are
        # never omitted (the empty case prints NONE_THIS_RUN — see TestReport).
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ARGS + ["plan"])
        for header in ("## Actions", "## Proposals", "## Watchlist",
                       "## Already dark", "## Guards"):
            assert header in result.output


class TestApply:
    def test_apply_without_confirm_write_dispatches_nothing(self, runner, fresh_adapter,
                                                             tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ARGS + ["apply"])
        assert result.exit_code == 0
        assert "nothing dispatched" in result.output
        assert fresh_adapter["instance"].writes == []

    def test_apply_confirm_write_still_respects_guards(self, runner, fresh_adapter,
                                                       tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ARGS + ["--confirm-write", "apply"])
        assert result.exit_code == 0
        # The demo fixture is healthy: guards allow, actions dispatch.
        assert fresh_adapter["instance"].writes

    def test_read_only_env_beats_confirm_write(self, runner, fresh_adapter, tmp_path,
                                               monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AGON_READ_ONLY", "1")
        result = runner.invoke(main, ARGS + ["--confirm-write", "apply"])
        assert result.exit_code == 0
        assert "AGON_READ_ONLY" in result.output
        assert fresh_adapter["instance"].writes == []

    def test_audit_writes_jsonl_even_on_dry_runs(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ARGS + ["apply"])
        assert result.exit_code == 0
        audit = tmp_path / "reports" / "write-audit.jsonl"
        assert audit.exists()
        entries = [json.loads(line) for line in
                   audit.read_text(encoding="utf-8").strip().splitlines()]
        assert entries
        assert all(e["outcome"] == "dry-run" for e in entries)


class TestOtherCommands:
    def test_baseline_lists_markets(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ARGS + ["baseline"])
        assert result.exit_code == 0
        assert "NZ" in result.output and "US" in result.output
        assert "seeded" in result.output

    def test_verify_confirms_post_id(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Duplicate then verify via the same adapter is CLI-level; here the
        # fixture's own two ads with distinct posts prove the negative path.
        result = runner.invoke(
            main, ARGS + ["verify", "ad_dead_1", "ad_dead_2"],
        )
        assert result.exit_code == 0  # same post p_dead on both

    def test_verify_fails_on_post_mismatch(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ARGS + ["verify", "ad_kill_a", "ad_kill_b"])
        assert result.exit_code != 0
        assert "VERIFY FAILED" in result.output

    def test_debate_prints_briefs(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ARGS + ["debate"])
        assert result.exit_code == 0
        assert result.output.strip()  # briefs or the none-this-run line

    def test_audit_reads_only(self, runner, fresh_adapter, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ARGS + ["audit"])
        assert result.exit_code == 0
        assert fresh_adapter["instance"].writes == []

    def test_fixture_requires_fixtures_path(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ["--config", str(CONFIG_PATH), "plan"])
        assert result.exit_code != 0
        assert "--fixtures" in result.output

    def test_missing_config_fails_cleanly(self, runner):
        result = runner.invoke(main, ["--config", "nowhere.yaml", "plan"])
        assert result.exit_code != 0
        assert "not found" in result.output


class TestReport:
    def test_empty_sections_say_none_this_run(self, config):
        from agon.guards import GuardVerdict
        from agon.pipeline import RunResult

        empty = RunResult(
            config=config,
            snapshot=FixtureAdapter(FIXTURES / "incomplete").fetch_entities(),
            baselines={},
            resolutions=[],
            actions=[],
            proposals=[],
            watchlist=[],
            already_dark=[],
            campaign_results=[],
            guard=GuardVerdict(writes_allowed=False, reasons=["x"], urgent=True),
            daily_spend={},
        )
        text = render_report(empty)
        assert text.count(NONE_THIS_RUN) >= 3
        assert "URGENT" in text

    def test_spend_header_leads_the_report(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ARGS + ["plan"])
        spend_at = result.output.index("Live daily spend by stage")
        actions_at = result.output.index("## Actions")
        assert spend_at < actions_at

    def test_delta_since_previous_run(self, config, adapter):
        from agon.pipeline import Pipeline

        run = Pipeline(adapter, config).run()
        previous = {"daily_spend": {k: v * 0.5 for k, v in run.daily_spend.items()}}
        text = render_report(run, previous=previous)
        assert "Δ since last run" in text


class TestDocumentedInvocations:
    """The exact command strings printed in docs/writes.md §5 and the README
    quickstart must run — the connection options are accepted at subcommand
    level, and a subcommand value wins when both levels supply one."""

    def _demo_dir(self, tmp_path):
        import shutil

        shutil.copytree(FIXTURES, tmp_path / "tests" / "fixtures")
        (tmp_path / "examples").mkdir()
        shutil.copy(CONFIG_PATH, tmp_path / "account.yaml")
        shutil.copy(CONFIG_PATH, tmp_path / "examples" / "config.example.yaml")
        return tmp_path

    @pytest.mark.parametrize(
        "argv",
        [
            ["audit", "--config", "account.yaml"],
            ["plan", "--config", "account.yaml"],
            ["debate", "--config", "account.yaml"],
            ["apply", "--config", "account.yaml"],
            ["apply", "--config", "account.yaml", "--confirm-write"],
        ],
    )
    def test_writes_md_section5_commands_run(self, runner, tmp_path, monkeypatch, argv):
        monkeypatch.chdir(self._demo_dir(tmp_path))
        monkeypatch.delenv("AGON_READ_ONLY", raising=False)
        result = runner.invoke(main, argv)
        assert result.exit_code == 0, result.output

    def test_readme_quickstart_fixture_plan(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(self._demo_dir(tmp_path))
        result = runner.invoke(
            main,
            ["plan", "--adapter", "fixture", "--fixtures", "tests/fixtures",
             "--config", "examples/config.example.yaml"],
        )
        assert result.exit_code == 0, result.output
        assert "# Agon run report" in result.output

    def test_readme_quickstart_readonly_commands(self, runner, tmp_path, monkeypatch):
        """audit/plan/debate exactly as the README prints them, with the
        read-only belt on."""
        monkeypatch.chdir(self._demo_dir(tmp_path))
        monkeypatch.setenv("AGON_READ_ONLY", "1")
        for command in ("audit", "plan", "debate"):
            result = runner.invoke(main, [command, "--config", "account.yaml"])
            assert result.exit_code == 0, (command, result.output)

    def test_subcommand_option_overrides_group(self, runner, tmp_path, monkeypatch):
        """Group loads the example config; the subcommand's --config wins.
        The subcommand file carries a broken target — only loading IT can
        produce the target error."""
        import yaml

        monkeypatch.chdir(self._demo_dir(tmp_path))
        payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        payload["target"] = 0  # invalid — ConfigError names the key
        (tmp_path / "account.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
        result = runner.invoke(
            main,
            ["--config", str(CONFIG_PATH), "baseline", "--config", "account.yaml"],
        )
        assert result.exit_code != 0
        assert "target" in result.output

    def test_group_option_still_used_when_subcommand_silent(self, runner, tmp_path,
                                                            monkeypatch):
        """No subcommand --config: the group's value applies (here: the
        broken file, proving it was the one loaded)."""
        monkeypatch.chdir(self._demo_dir(tmp_path))
        import yaml

        payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        payload["target"] = 0
        (tmp_path / "account.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
        result = runner.invoke(main, ["--config", "account.yaml", "baseline"])
        assert result.exit_code != 0
        assert "target" in result.output


class TestGuardTripExitCode:
    def test_guard_trip_exits_two(self, runner, tmp_path, monkeypatch):
        """A tripped breaker is exit 2, not 0 — cron must detect it."""
        monkeypatch.chdir(tmp_path)
        args = ["--config", str(CONFIG_PATH), "--adapter", "fixture",
                "--fixtures", str(FIXTURES / "incomplete")]
        result = runner.invoke(main, args + ["apply"])
        assert result.exit_code == 2
        assert "GUARD TRIP" in result.output

    def test_healthy_run_exits_zero(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ARGS + ["apply"])
        assert result.exit_code == 0


class TestDeltaOrdering:
    def test_delta_compared_against_previous_run_not_itself(self, runner, tmp_path,
                                                            monkeypatch):
        """The §8 delta must compare against the PREVIOUS run's audit header.
        Rewrite that header's spend and the next report must show the shift;
        reading it after dispatch would compare the run against itself."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AGON_READ_ONLY", raising=False)
        first = runner.invoke(main, ARGS + ["apply"])
        assert first.exit_code == 0, first.output
        audit = tmp_path / "reports" / "write-audit.jsonl"
        lines = audit.read_text(encoding="utf-8").strip().splitlines()
        entry = json.loads(lines[-1])
        entry["daily_spend"] = {"SCALE": 1.0}
        lines[-1] = json.dumps(entry)
        audit.write_text("\n".join(lines) + "\n", encoding="utf-8")
        second = runner.invoke(main, ARGS + ["apply"])
        assert second.exit_code == 0, second.output
        # SCALE daily spend is (800 + 500) / 7 ≈ 185.71; from 1.00 that is
        # +184.71 — impossible if the run were compared against itself.
        assert "+184.71" in second.output


class TestReportFixes:
    def test_scorecard_reads_enriched_ads(self, config, adapter):
        """The stage map is applied by the pipeline; the scorecard must read
        the enriched ads or the whole live-adapter scorecard prints UNMAPPED."""
        from dataclasses import replace

        from agon.guards import GuardVerdict
        from agon.pipeline import Pipeline, RunResult

        run = Pipeline(adapter, config).run()
        raw = replace(run.snapshot, ads=[replace(a, stage=None) for a in run.snapshot.ads])
        staged = RunResult(
            config=config, snapshot=raw, baselines=run.baselines,
            resolutions=run.resolutions, actions=run.actions,
            proposals=run.proposals, watchlist=run.watchlist,
            already_dark=run.already_dark, campaign_results=run.campaign_results,
            guard=GuardVerdict(writes_allowed=True), daily_spend=run.daily_spend,
            ads=run.ads,
        )
        text = render_report(staged)
        assert "UNMAPPED" not in text.split("## Stage scorecard")[1].split("##")[0]
        assert "PROVING" in text

    def test_downgraded_actions_printed_under_proposals(self, config, adapter):
        from agon.guards import GuardVerdict
        from agon.pipeline import Pipeline, RunResult

        run = Pipeline(adapter, config).run()
        downgraded = [a.as_proposal("proposed only (§5 Path B ceiling)")
                      for a in run.actions[:1]]
        result = RunResult(
            config=config, snapshot=run.snapshot, baselines=run.baselines,
            resolutions=run.resolutions, actions=downgraded,
            proposals=run.proposals, watchlist=run.watchlist,
            already_dark=run.already_dark, campaign_results=run.campaign_results,
            guard=GuardVerdict(writes_allowed=True), daily_spend=run.daily_spend,
            ads=run.ads,
        )
        text = render_report(result)
        actions_section = text.split("## Actions")[1].split("## Proposals")[0]
        proposals_section = text.split("## Proposals")[1].split("## Watchlist")[0]
        assert "None this run" in actions_section
        assert downgraded[0].verb in proposals_section

    def test_derived_floats_rounded_to_two_decimals(self, config, adapter):
        from agon.guards import GuardVerdict
        from agon.pipeline import Pipeline

        run = Pipeline(adapter, config).run()
        text = render_report(run)
        assert "4.0043999999999995" not in text
        assert "4.0" in text or "4.00" in text
