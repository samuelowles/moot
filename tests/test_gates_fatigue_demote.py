"""FATIGUE (§6), DEMOTE (§7) and the §7.1 auction check."""

from __future__ import annotations

from conftest import make_ad, make_ctx

from agon.gates.base import auction_check
from agon.gates.demote import DemoteGate
from agon.gates.fatigue import FatigueGate, WatchGate
from agon.models import CreativeType, Decision, Metrics, Stage


def simple_fatigue(recent_value=300.0, trailing_value=5600.0,
                   recent_carts=7, trailing_carts=70):
    """Conditions 1–4 hold by construction (recent return 1.5 < 4.0 floor,
    lifetime 10 purchases, age 20, recent spend 200); the decline is whatever
    the caller's numbers imply."""
    return make_ad(
        "f", creative=CreativeType.VIDEO, age_days=20,
        recent=Metrics(spend=200.0, carts=recent_carts, purchases=2,
                       purchase_value=recent_value, cpm=8.0),
        trailing=Metrics(spend=700.0, carts=trailing_carts, purchases=8,
                         purchase_value=trailing_value, cpm=12.0),
        lifetime=Metrics(spend=900.0, purchases=10),
    )


class TestFatigue:
    def test_fires_on_own_history_decline(self, config):
        # recent cpc 28.6 ≥ 1.3 × 10 and return 1.5 < 0.6 × 8 — both declines.
        ad = simple_fatigue(recent_value=300.0, trailing_value=5600.0)
        results = FatigueGate().evaluate(ad, make_ctx(config))
        assert [r.decision for r in results] == [Decision.FATIGUE]
        assert results[0].evidence["decline_reasons"]

    def test_decline_on_cost_alone(self, config):
        # return flat (2.0 vs 2.0) but cpc rose 10 → 30.
        ad = simple_fatigue(recent_value=400.0, trailing_value=1400.0,
                            recent_carts=7, trailing_carts=35)
        results = FatigueGate().evaluate(ad, make_ctx(config))
        assert [r.decision for r in results] == [Decision.FATIGUE]

    def test_below_floor_without_decline_is_watch_not_fatigue(self, config):
        """§6: an ad failing 1–4 with no decline against its own baseline goes
        on the watchlist, not to the Reserve."""
        # recent return 3.0 < 4.0 floor, but ≥ 0.6 × trailing 3.2 and cpc
        # 12 < 1.3 × 10 — a bad week, not a terminal one.
        ad = simple_fatigue(recent_value=600.0, trailing_value=960.0,
                            recent_carts=25, trailing_carts=30)
        assert FatigueGate().evaluate(ad, make_ctx(config)) == []
        watch = WatchGate().evaluate(ad, make_ctx(config))
        assert [r.decision for r in watch] == [Decision.WATCH]

    def test_no_watch_when_healthy(self, config):
        ad = simple_fatigue(recent_value=1200.0, trailing_value=2800.0,
                            recent_carts=20, trailing_carts=35)
        assert WatchGate().evaluate(ad, make_ctx(config)) == []
        assert FatigueGate().evaluate(ad, make_ctx(config)) == []

    def test_unproven_ad_does_not_fatigue(self, config):
        # lifetime purchases 2 < 3: only proven creative enters the Reserve.
        ad = make_ad(
            "f", creative=CreativeType.VIDEO, age_days=20,
            recent=Metrics(spend=200.0, carts=7, purchases=2, purchase_value=300.0),
            trailing=Metrics(spend=700.0, carts=70, purchases=8, purchase_value=5600.0),
            lifetime=Metrics(spend=900.0, purchases=2),
        )
        assert FatigueGate().evaluate(ad, make_ctx(config)) == []

    def test_young_ad_does_not_fatigue(self, config):
        ad = make_ad(
            "f", creative=CreativeType.VIDEO, age_days=6,
            recent=Metrics(spend=200.0, carts=7, purchases=2, purchase_value=300.0),
            trailing=Metrics(spend=700.0, carts=70, purchases=8, purchase_value=5600.0),
            lifetime=Metrics(spend=900.0, purchases=10),
        )
        assert FatigueGate().evaluate(ad, make_ctx(config)) == []

    def test_scale_ad_is_not_fatigued(self, config):
        ad = make_ad(
            "f", stage=Stage.SCALE, campaign_id="120000000000004",
            creative=CreativeType.VIDEO, age_days=20,
            recent=Metrics(spend=200.0, carts=7, purchases=2, purchase_value=300.0),
            trailing=Metrics(spend=700.0, carts=70, purchases=8, purchase_value=5600.0),
            lifetime=Metrics(spend=900.0, purchases=10),
        )
        assert FatigueGate().evaluate(ad, make_ctx(config)) == []


class TestDemote:
    def AD(self, recent_value=640.0, age=20):
        return make_ad(
            "dm", stage=Stage.SCALE, campaign_id="120000000000004",
            creative=CreativeType.VIDEO, age_days=age,
            recent=Metrics(spend=200.0, carts=20, purchases=3,
                           purchase_value=recent_value, cpm=10.0),
            trailing=Metrics(spend=800.0, carts=80, purchases=9,
                             purchase_value=3200.0, cpm=10.0, outbound_ctr=0.015),
            lifetime=Metrics(spend=1200.0, purchases=9),
        )

    def test_fires_on_scale_ad(self, config):
        results = DemoteGate().evaluate(self.AD(), make_ctx(config))
        assert [r.decision for r in results] == [Decision.DEMOTE]

    def test_not_before_min_age(self, config):
        # §7: judging before the attribution window closes reads far worse
        # than the ad is.
        assert DemoteGate().evaluate(self.AD(age=6), make_ctx(config)) == []

    def test_not_above_floor(self, config):
        assert DemoteGate().evaluate(self.AD(recent_value=900.0), make_ctx(config)) == []

    def test_proving_ad_is_not_demoted(self, config):
        ad = make_ad(
            "dm", creative=CreativeType.VIDEO, age_days=20,
            recent=Metrics(spend=200.0, carts=20, purchases=3, purchase_value=640.0),
            trailing=Metrics(spend=800.0, carts=80, purchases=9, purchase_value=3200.0),
            lifetime=Metrics(spend=1200.0, purchases=9),
        )
        assert DemoteGate().evaluate(ad, make_ctx(config)) == []


class TestAuctionCheck:
    def auction_ad(self, *, recent_ctr=0.0145, trailing_ctr=0.015,
                   recent_cpm=20.0, trailing_cpm=12.0,
                   recent_value=700.0, trailing_value=3600.0):
        return make_ad(
            "auc", creative=CreativeType.VIDEO, age_days=15,
            recent=Metrics(
                spend=200.0, carts=7, purchases=2, purchase_value=recent_value,
                outbound_ctr=recent_ctr, cpm=recent_cpm,
                impressions=10000, video_3s=2500,
            ),
            trailing=Metrics(
                spend=600.0, carts=60, purchases=6, purchase_value=trailing_value,
                outbound_ctr=trailing_ctr, cpm=trailing_cpm,
                impressions=30000, video_3s=7800,
            ),
            lifetime=Metrics(spend=800.0, purchases=5),
        )

    def test_stable_ctr_risen_cpm_falling_return_fires(self, config):
        result = auction_check(self.auction_ad())
        assert result is not None
        assert result.evidence["auction_shift"] is True
        assert result.decision is Decision.BUDGET_UP

    def test_flat_cpm_does_not_fire(self, config):
        ad = self.auction_ad(recent_cpm=12.0, trailing_cpm=12.0)
        assert auction_check(ad) is None

    def test_collapsing_ctr_does_not_fire(self, config):
        ad = self.auction_ad(recent_ctr=0.005, trailing_ctr=0.015)
        assert auction_check(ad) is None

    def test_rising_return_does_not_fire(self, config):
        ad = self.auction_ad(recent_value=4000.0, trailing_value=3600.0)
        assert auction_check(ad) is None

    def test_hook_undefined_for_static_is_skipped(self, config):
        ad = make_ad(
            "auc", creative=CreativeType.STATIC, age_days=15,
            recent=Metrics(spend=200.0, carts=7, purchases=2, purchase_value=700.0,
                           outbound_ctr=0.0145, cpm=20.0),
            trailing=Metrics(spend=600.0, carts=60, purchases=6, purchase_value=3600.0,
                             outbound_ctr=0.015, cpm=12.0),
            lifetime=Metrics(spend=800.0, purchases=5),
        )
        assert ad.recent.hook_rate is None
        assert auction_check(ad) is not None  # hook clause skipped, not failed


class TestDemoteOwnFloors:
    """§7 reads gates.demote — a spend of 160 clears the §6 fatigue floor
    (150) but not a configured demote floor of 200."""

    def _ad(self):
        return make_ad(
            "dm", stage=Stage.SCALE, campaign_id="120000000000004",
            creative=CreativeType.VIDEO, age_days=20,
            recent=Metrics(spend=160.0, carts=20, purchases=3,
                           purchase_value=640.0, cpm=10.0),
            trailing=Metrics(spend=800.0, carts=80, purchases=9,
                             purchase_value=3200.0, cpm=10.0, outbound_ctr=0.015),
            lifetime=Metrics(spend=1200.0, purchases=9),
        )

    def test_demote_floor_of_200_blocks_the_demotion(self, config):
        from dataclasses import replace as dc_replace

        from agon.config import DemoteGates

        narrow = dc_replace(config, demote=DemoteGates(min_spend=200.0))
        assert DemoteGate().evaluate(self._ad(), make_ctx(narrow)) == []

    def test_default_floor_of_150_still_demotes(self, config):
        assert DemoteGate().evaluate(self._ad(), make_ctx(config))
