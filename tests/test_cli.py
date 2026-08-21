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
