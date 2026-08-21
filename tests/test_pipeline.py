"""End-to-end pipeline runs on the demo fixture: §12 precedence, guards
feeding, actions and proposals."""

from __future__ import annotations

import pytest
from conftest import RUN_NOW

from agon.models import Decision
from agon.pipeline import Pipeline


@pytest.fixture()
def run(adapter, config):
    return Pipeline(adapter, config).run(now=RUN_NOW)


def decision_for(run, ad_id):
    for resolution in run.resolutions:
        if resolution.ad.id == ad_id:
            return resolution
    raise AssertionError(f"ad {ad_id} not in resolutions")


def actions_for(run, ad_id):
    return [a for a in run.actions + run.proposals if a.target_id == ad_id]


class TestPrecedence:
    def test_fatigue_beats_kill_and_records_loser(self, run):
        """§12 rank 4 over rank 5: an ad matching both FATIGUE and KILL
        retires to the Reserve; the winning transition records the losing
        gate in suppressed_by."""
        resolution = decision_for(run, "ad_kill_c2")
        assert resolution.winner.decision is Decision.FATIGUE
        assert resolution.winner.suppressed_by is Decision.KILL
        loser_decisions = [loser.decision for loser in resolution.losers]
        assert Decision.KILL in loser_decisions

    def test_exactly_one_transition_per_ad(self, run):
        for resolution in run.resolutions:
            if resolution.winner is not None:
                assert resolution.winner.decision is not Decision.NONE
                assert all(
                    loser.decision != resolution.winner.decision
                    for loser in resolution.losers
                )

    def test_pure_kill_ad_dies(self, run):
        resolution = decision_for(run, "ad_kill_c1")
        assert resolution.winner.decision is Decision.KILL
        pauses = [a for a in run.actions if a.target_id == "ad_kill_c1"]
        assert pauses and pauses[0].verb == "ad.pause"

    def test_auction_shift_produces_budget_proposal_not_retirement(self, run):
        """§7.1 via §12 rank 3: stable CTR + risen CPM + falling return must
        convert the retirement into a budget proposal."""
        resolution = decision_for(run, "ad_auction")
        assert resolution.winner.decision is Decision.BUDGET_UP
        assert resolution.winner.evidence["auction_shift"] is True
        assert resolution.winner.suppressed_by is Decision.FATIGUE
        proposals = [a for a in run.proposals if a.target_id == "ad_auction"]
        assert proposals and proposals[0].verb == "budget.bid_review"
        # The ad is NOT paused, duplicated, or retired.
        assert not [a for a in run.actions if a.target_id == "ad_auction"]

    def test_watch_ad_takes_no_action(self, run):
        resolution = decision_for(run, "ad_watch")
        assert resolution.winner.decision is Decision.WATCH
        assert not actions_for(run, "ad_watch")
        assert any(w.entity_id == "ad_watch" for w in run.watchlist)


class TestAlreadyDark:
    def test_dark_ads_reported_not_judged(self, run):
        assert [ad.id for ad in run.already_dark] == ["ad_dark"]
        assert not any(r.ad.id == "ad_dark" for r in run.resolutions)


class TestGraduation:
    def test_graduate_emits_duplicate_not_pause(self, run):
        """§5 / framework.md §2.1: the graduating ad does not leave the
        Proving Ground — copy up, never pause the source."""
        resolution = decision_for(run, "ad_graduate_a")
        assert resolution.winner.decision is Decision.GRADUATE
        got = actions_for(run, "ad_graduate_a")
        verbs = [a.verb for a in got]
        assert "duplicate.post_id" in verbs
        assert "ad.pause" not in verbs
        # The cohort ad set is created because it is absent; its action
        # targets the destination campaign, not the ad.
        cohort = [a for a in run.actions if a.verb == "adset.create_cohort"]
        assert cohort and cohort[0].params["born"] == "PAUSED"
        assert cohort[0].target_id == "120000000000004"

    def test_seeded_market_graduates_nothing(self, run):
        """§3: US is seeded from AU → kill gates only, even though
        ad_us_graduate would pass every graduation gate."""
        assert not any(
            r.winner and r.winner.decision is Decision.GRADUATE
            for r in run.resolutions
            if r.ad.market == "US"
        )


class TestRetirement:
    def test_fatigue_retires_via_duplication_then_pause(self, run):
        got = [a.verb for a in actions_for(run, "ad_fatigue")]
        assert "duplicate.post_id" in got and "ad.pause" in got

    def test_demote_duplication_targets_reserve(self, run):
        duplicates = [
            a for a in actions_for(run, "ad_demote") if a.verb == "duplicate.post_id"
        ]
        assert duplicates
        assert duplicates[0].params["destination_stage"] == "RESERVE"
        assert duplicates[0].params["url_tags"] is not None  # §9 C carried

    def test_reserve_reactivation_fires(self, run):
        recovers = [
            r for r in run.campaign_results if r.decision is Decision.RECOVER
        ]
        assert recovers
        reactivate = [a for a in run.actions if a.verb == "reserve.reactivate"]
        assert reactivate and reactivate[0].target_id == "as_nz_retired"


class TestBudgetActions:
    def test_scale_up_capped_at_thirty(self, run):
        ups = [a for a in run.actions if a.verb == "campaign.budget_increase"]
        assert ups
        assert ups[0].params["pct"] == 25.0  # config step, under the cap
        assert ups[0].params["new_daily_budget"] == pytest.approx(400 * 1.25)

    def test_scale_down_emitted(self, run):
        downs = [a for a in run.actions if a.verb == "campaign.budget_decrease"]
        assert downs and downs[0].target_id == "120000000000005"

    def test_adset_increase_reported_not_executed(self, run):
        """§8: ad-set budgets are frozen to autonomous increase."""
        assert not [a for a in run.actions if "adset" in a.verb and "budget" in a.verb]


class TestGuardsWiring:
    def test_guards_allow_writes_on_healthy_fixture(self, run):
        assert run.guard.writes_allowed is True
        assert run.guard.urgent is False

    def test_daily_spend_by_stage_computed(self, run):
        assert set(run.daily_spend) >= {"PROVING", "SCALE", "RESERVE"}
        # NZ scale: recent spend 800 over 7 days.
        assert run.daily_spend["SCALE"] == pytest.approx((800 + 500) / 7)


class TestPreflightIntegration:
    def test_preflights_recorded(self, run):
        assert run.preflights
        statuses = {pf.status for pf in run.preflights}
        assert "ok" in statuses

    def test_source_never_paused_on_graduation(self, run):
        # "Do not pause the source" — framework.md §2.1, restated.
        for ad_id in ("ad_graduate_a", "ad_static_graduate"):
            assert "ad.pause" not in [a.verb for a in actions_for(run, ad_id)]
