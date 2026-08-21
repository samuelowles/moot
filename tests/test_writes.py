"""The write safety layer: dry-run default, read-only env, envelope,
budget cap, audit JSONL, verification. Fixture adapter only — no network."""

from __future__ import annotations

import json

import pytest
from conftest import FIXTURES

from agon.adapters.fixture import FixtureAdapter
from agon.guards import GuardVerdict
from agon.models import Action
from agon.writes import (
    BUDGET_INCREASE_HARD_CAP_PCT,
    DRY_RUN,
    PROPOSED,
    SKIPPED_READ_ONLY,
    dispatch,
    read_only_env,
)

ALLOWED = GuardVerdict(writes_allowed=True)
BLOCKED = GuardVerdict(writes_allowed=False, reasons=["CIRCUIT BREAKER 2: partial pull"])


def run_dispatch(adapter, config, actions, *, confirm_write=False, audit=None):
    return dispatch(
        actions, adapter, config, ALLOWED,
        confirm_write=confirm_write, audit_path=audit,
    )


class TestDryRunDefault:
    def test_dry_run_dispatches_nothing(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(adapter, config, [Action(verb="ad.pause", target_id="ad_kill_a")])
        assert adapter.writes == []
        assert all(o.outcome == DRY_RUN for o in result.outcomes)
        assert result.dispatched_count == 0

    def test_confirm_write_dispatches(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config, [Action(verb="ad.pause", target_id="ad_kill_a")],
            confirm_write=True,
        )
        assert adapter.writes, "confirm_write with allowed guards must dispatch"
        assert result.dispatched_count == 1

    def test_guard_blocks_override_confirm(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = dispatch(
            [Action(verb="ad.pause", target_id="ad_kill_a")], adapter, config, BLOCKED,
            confirm_write=True,
        )
        assert adapter.writes == []
        assert all(o.outcome == "skipped-guard" for o in result.outcomes)


class TestReadOnlyEnv:
    def test_read_only_forces_propose_only_over_confirm_write(self, config, monkeypatch):
        """AGON_READ_ONLY=1 beats --confirm-write: nothing dispatches."""
        monkeypatch.setenv("AGON_READ_ONLY", "1")
        adapter = FixtureAdapter(FIXTURES)
        result = dispatch(
            [Action(verb="ad.pause", target_id="ad_kill_a")], adapter, config, ALLOWED,
            confirm_write=True,
        )
        assert adapter.writes == []
        assert all(o.outcome == SKIPPED_READ_ONLY for o in result.outcomes)
        assert result.dispatched_count == 0

    def test_read_only_env_parsed(self, monkeypatch):
        monkeypatch.setenv("AGON_READ_ONLY", "1")
        assert read_only_env() is True
        monkeypatch.setenv("AGON_READ_ONLY", "")
        assert read_only_env() is False


class TestEnvelope:
    def test_out_of_envelope_downgraded_not_executed(self, config):
        adapter = FixtureAdapter(FIXTURES)
        sneaky = Action(
            verb="adset.budget_increase", target_id="as_nz_p1", authorized=True
        )
        result = run_dispatch(adapter, config, [sneaky], confirm_write=True)
        assert adapter.writes == []
        assert result.outcomes[0].outcome == PROPOSED
        assert "envelope" in result.outcomes[0].detail

    def test_forbidden_verb_downgraded(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config,
            [Action(verb="campaign.create", target_id="x", authorized=True)],
            confirm_write=True,
        )
        assert adapter.writes == []
        assert result.outcomes[0].outcome == PROPOSED

    def test_unauthorized_action_flag_downgraded(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config,
            [Action(verb="ad.pause", target_id="a", authorized=False)],
            confirm_write=True,
        )
        assert adapter.writes == []
        assert result.outcomes[0].outcome == PROPOSED


class TestBudgetCap:
    def test_budget_step_clamped_to_thirty_percent(self, config):
        """§8: the cap is in CODE. A plan asking +50% dispatches +30%."""
        adapter = FixtureAdapter(FIXTURES)
        action = Action(
            verb="campaign.budget_increase", target_id="120000000000004",
            params={"pct": 50.0, "current_daily_budget": 400.0, "new_daily_budget": 600.0},
        )
        run_dispatch(adapter, config, [action], confirm_write=True)
        assert adapter.writes
        budget_write = adapter.writes[0]
        assert budget_write["daily_amount"] == pytest.approx(400 * 1.30)
        assert BUDGET_INCREASE_HARD_CAP_PCT == 30.0

    def test_clamp_note_recorded(self, config):
        adapter = FixtureAdapter(FIXTURES)
        action = Action(
            verb="campaign.budget_increase", target_id="120000000000004",
            params={"pct": 45.0, "current_daily_budget": 100.0, "new_daily_budget": 145.0},
        )
        result = run_dispatch(adapter, config, [action], confirm_write=True)
        assert "clamped" in result.outcomes[0].detail

    def test_step_within_cap_untouched(self, config):
        adapter = FixtureAdapter(FIXTURES)
        action = Action(
            verb="campaign.budget_increase", target_id="120000000000004",
            params={"pct": 25.0, "current_daily_budget": 400.0, "new_daily_budget": 500.0},
        )
        run_dispatch(adapter, config, [action], confirm_write=True)
        assert adapter.writes[0]["daily_amount"] == pytest.approx(500.0)


class TestAudit:
    def test_audit_jsonl_written_for_dry_runs(self, config, tmp_path):
        audit = tmp_path / "audit.jsonl"
        run_dispatch(
            FixtureAdapter(FIXTURES), config,
            [Action(verb="ad.pause", target_id="ad_kill_a")],
            audit=audit,
        )
        lines = audit.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["verb"] == "ad.pause"
        assert entry["outcome"] == DRY_RUN
        assert entry["confirm_write"] is False

    def test_audit_records_every_action_including_skips(self, config, tmp_path, monkeypatch):
        monkeypatch.setenv("AGON_READ_ONLY", "1")
        audit = tmp_path / "audit.jsonl"
        actions = [
            Action(verb="ad.pause", target_id="a1"),
            Action(verb="campaign.budget_decrease", target_id="c1"),
        ]
        run_dispatch(FixtureAdapter(FIXTURES), config, actions, audit=audit,
                     confirm_write=True)
        entries = [json.loads(line) for line in
                   audit.read_text(encoding="utf-8").strip().splitlines()]
        assert len(entries) == 2
        assert all(e["outcome"] == SKIPPED_READ_ONLY for e in entries)

    def test_no_audit_file_no_crash(self, config):
        result = run_dispatch(
            FixtureAdapter(FIXTURES), config, [Action(verb="ad.pause", target_id="a")]
        )
        assert result.audit_path is None
        assert result.outcomes[0].outcome == DRY_RUN


class TestNoDelete:
    def test_delete_verb_refused(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config, [Action(verb="ad.delete", target_id="x")],
            confirm_write=True,
        )
        assert adapter.writes == []
        assert result.outcomes[0].outcome == "failed"
        assert "no delete verb" in result.outcomes[0].detail


class TestVerification:
    def test_pause_verified_by_re_read(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config, [Action(verb="ad.pause", target_id="ad_kill_a")],
            confirm_write=True,
        )
        verify = result.outcomes[0].verify
        assert verify and verify["verified"] is True
        assert adapter.get_ad("ad_kill_a").effective_status == "PAUSED"

    def test_duplicate_chain_verifies_post_id(self, config):
        adapter = FixtureAdapter(FIXTURES)
        create = Action(
            verb="adset.create_cohort", target_id="120000000000004",
            params={"name": "2026-08 winners", "campaign_id": "120000000000004",
                    "pixel_id": "400000000000001"},
        )
        duplicate = Action(
            verb="duplicate.post_id", target_id="ad_graduate_a",
            params={
                "destination_campaign_id": "120000000000004",
                "destination_adset_id": None,
                "destination_adset_name": "2026-08 winners",
                "destination_stage": "SCALE",
                "page_id": "100000000000001",
                "activate_after_verify": True,
            },
        )
        result = run_dispatch(adapter, config, [create, duplicate], confirm_write=True)
        assert result.outcomes[0].outcome == "dispatched"
        outcome = result.outcomes[1]
        assert outcome.outcome == "dispatched"
        new_id = outcome.verify["new_ad_id"]
        assert adapter.get_ad(new_id).post_id == "p_g1"
        assert outcome.verify["activation"]["verified"] is True

    def test_duplicate_without_destination_adset_is_proposed(self, config):
        adapter = FixtureAdapter(FIXTURES)
        action = Action(
            verb="duplicate.post_id", target_id="ad_graduate_a",
            params={"destination_campaign_id": "120000000000004",
                    "destination_stage": "SCALE", "page_id": "100000000000001"},
        )
        result = run_dispatch(adapter, config, [action], confirm_write=True)
        assert result.outcomes[0].outcome == "proposed"
        assert adapter.writes == []

    def test_url_tags_survive_the_copy(self, config):
        adapter = FixtureAdapter(FIXTURES)
        action = Action(
            verb="duplicate.post_id", target_id="ad_fatigue",
            params={
                "destination_campaign_id": "120000000000006",
                "destination_adset_id": "as_nz_retired",
                "destination_stage": "RESERVE",
                "page_id": "100000000000001",
            },
        )
        result = run_dispatch(adapter, config, [action], confirm_write=True)
        new_id = result.outcomes[0].verify["new_ad_id"]
        assert adapter.get_ad(new_id).url_tags == "utm_source=meta&utm_campaign=proving"
