"""Guards — docs/gates.md §10: anomaly guard and the three breakers."""

from __future__ import annotations

import pytest
from conftest import fixture_adapter

from moot.guards import RunSnapshot, evaluate_guards
from moot.models import Action


def pause(ad_id="a1"):
    return Action(verb="ad.pause", target_id=ad_id)


def verdict(actions, snapshot, config):
    return evaluate_guards(actions, snapshot, config)


class TestAnomalyGuard:
    def test_blocks_writes_above_half_of_pipeline_spend(self, config):
        # 600 of 1000 recent spend would go dark: 60% > 50%.
        snapshot = RunSnapshot(
            pull_complete=True,
            account_recent_return=4.0,
            pipeline_recent_spend=1000.0,
            paused_recent_spend=600.0,
        )
        result = verdict([pause()], snapshot, config)
        assert result.writes_allowed is False
        assert result.urgent is True
        assert any("ANOMALY GUARD" in reason for reason in result.reasons)

    def test_exactly_fifty_percent_does_not_trip(self, config):
        # The rule is ">" — 50% exactly is not above it.
        snapshot = RunSnapshot(
            pull_complete=True,
            account_recent_return=4.0,
            pipeline_recent_spend=1000.0,
            paused_recent_spend=500.0,
        )
        assert verdict([], snapshot, config).writes_allowed is True

    def test_evidence_carries_the_share(self, config):
        snapshot = RunSnapshot(
            pull_complete=True,
            account_recent_return=4.0,
            pipeline_recent_spend=400.0,
            paused_recent_spend=300.0,
        )
        result = verdict([], snapshot, config)
        assert result.evidence["paused_spend_share"] == pytest.approx(0.75)


class TestCircuitBreakers:
    def test_incomplete_pull_blocks_writes(self, config):
        """§10 breaker 2 — an incomplete pull means no writes this run,
        however sound the actions look."""
        snapshot = RunSnapshot(
            pull_complete=False,
            pull_errors=("page 2 of /ads timed out",),
            account_recent_return=6.0,
        )
        result = verdict([pause()], snapshot, config)
        assert result.writes_allowed is False
        assert any("pull incomplete" in r for r in result.reasons)

    def test_account_return_below_floor_blocks_writes(self, config):
        snapshot = RunSnapshot(pull_complete=True, account_recent_return=1.5)
        result = verdict([pause()], snapshot, config)
        assert result.writes_allowed is False
        assert any("CIRCUIT BREAKER 1" in r for r in result.reasons)

    def test_unreported_account_return_fails_closed(self, config):
        snapshot = RunSnapshot(pull_complete=True, account_recent_return=None)
        assert verdict([pause()], snapshot, config).writes_allowed is False

    def test_healthy_run_allows_writes(self, config):
        snapshot = RunSnapshot(
            pull_complete=True, account_recent_return=4.0,
            pipeline_recent_spend=1000.0, paused_recent_spend=100.0,
        )
        result = verdict([pause()], snapshot, config)
        assert result.writes_allowed is True and result.reasons == []


class TestPipelineWiring:
    def test_incomplete_fixture_run_blocks_writes(self, config):
        from moot.pipeline import Pipeline

        result = Pipeline(fixture_adapter("incomplete"), config).run()
        assert result.guard.writes_allowed is False

    def test_fixture_adapter_surfaces_pull_state(self):
        snapshot = fixture_adapter("incomplete").fetch_entities()
        assert snapshot.pull_complete is False
        assert snapshot.errors == ["page 2 of /ads timed out"]


class TestSpendUnknown:
    """§10: unreported spend is a partial-data signal. It is excluded from
    the anomaly-share denominator (never ``or 0``-ed in) and a large unknown
    share is itself a data-quality trip."""

    def _snapshot(self, **kw):
        base = {
            "pull_complete": True, "account_recent_return": 4.0,
            "pipeline_recent_spend": 1000.0, "paused_recent_spend": 100.0,
        }
        base.update(kw)
        return RunSnapshot(**base)

    def test_unknown_above_a_fifth_of_known_trips(self, config):
        result = verdict([], self._snapshot(spend_unknown=300.0, spend_unknown_ads=3), config)
        assert result.writes_allowed is False
        assert result.urgent is True
        assert any("no recent spend" in r for r in result.reasons)

    def test_unknown_exactly_a_fifth_does_not_trip(self, config):
        # Strictly greater than 20% of known spend — 20% exactly is fine.
        result = verdict([], self._snapshot(spend_unknown=200.0, spend_unknown_ads=2), config)
        assert result.writes_allowed is True

    def test_small_unknown_share_allows_writes(self, config):
        result = verdict([], self._snapshot(spend_unknown=100.0, spend_unknown_ads=1), config)
        assert result.writes_allowed is True

    def test_unknown_surfaced_in_evidence(self, config):
        result = verdict([], self._snapshot(spend_unknown=300.0, spend_unknown_ads=3), config)
        assert result.evidence["spend_unknown"] == 300.0
        assert result.evidence["spend_unknown_ads"] == 3
