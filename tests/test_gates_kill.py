"""KILL limbs — docs/gates.md §4, each limb isolated."""

from __future__ import annotations

import pytest
from conftest import make_ad, make_ctx

from moot.baselines import MarketBaseline
from moot.gates.kill import KillGate
from moot.models import CreativeType, Decision, Metrics


def kill(ad, ctx):
    return KillGate().evaluate(ad, ctx)


class TestLimbA:
    def test_fires_never_worked(self, config):
        ad = make_ad(
            "a", age_days=10,
            trailing=Metrics(spend=50.0),
            lifetime=Metrics(spend=50.0),
        )
        results = kill(ad, make_ctx(config))
        assert [r.evidence["limb"] for r in results] == ["A"]
        assert results[0].decision is Decision.KILL

    def test_not_below_spend_floor(self, config):
        ad = make_ad("a", age_days=10, trailing=Metrics(spend=29.0))
        assert kill(ad, make_ctx(config)) == []

    def test_not_with_carts(self, config):
        ad = make_ad("a", age_days=10, trailing=Metrics(spend=50.0, carts=1))
        assert kill(ad, make_ctx(config)) == []

    def test_not_below_age_two_days(self, config):
        ad = make_ad("a", age_days=1, trailing=Metrics(spend=50.0))
        assert kill(ad, make_ctx(config)) == []


class TestLimbB:
    def test_fires_expensive_and_unconverting(self, config):
        ad = make_ad(
            "b", age_days=10,
            trailing=Metrics(spend=90.0, carts=3),  # cpc 30 > 1.30 × baseline 10
            recent=Metrics(spend=40.0),
            lifetime=Metrics(spend=90.0),
        )
        results = kill(ad, make_ctx(config))
        assert "B" in [r.evidence["limb"] for r in results]

    def test_not_when_cpc_within_baseline(self, config):
        ad = make_ad(
            "b", age_days=10,
            trailing=Metrics(spend=90.0, carts=9),  # cpc 10 ≤ 13
            recent=Metrics(spend=40.0),
        )
        assert kill(ad, make_ctx(config)) == []

    def test_not_with_purchases(self, config):
        ad = make_ad(
            "b", age_days=10,
            trailing=Metrics(spend=90.0, carts=3, purchases=1),
            recent=Metrics(spend=40.0),
        )
        assert kill(ad, make_ctx(config)) == []


class TestLimbC1:
    def test_fires_stalled(self, config):
        ad = make_ad(
            "c1", age_days=10,
            recent=Metrics(spend=200.0),
            trailing=Metrics(spend=300.0, carts=6, purchases=1),  # stale conversion
            lifetime=Metrics(spend=300.0, purchases=1),
        )
        results = kill(ad, make_ctx(config))
        assert "C1" in [r.evidence["limb"] for r in results]

    def test_not_with_recent_purchase(self, config):
        ad = make_ad(
            "c1", age_days=10,
            recent=Metrics(spend=200.0, purchases=1),
            trailing=Metrics(spend=300.0, carts=6, purchases=1),
            lifetime=Metrics(spend=300.0, purchases=1),
        )
        assert kill(ad, make_ctx(config)) == []


class TestLimbC2:
    def ad(self, value="150.0"):
        return make_ad(
            "c2", age_days=10,
            recent=Metrics(spend=200.0, purchases=2, purchase_value=float(value), carts=2),
            trailing=Metrics(spend=400.0, carts=8, purchases=3,
                             purchase_value=300.0, cpm=12.0),
            lifetime=Metrics(spend=600.0, purchases=3),
        )

    def test_fires_uneconomic(self, config):
        results = kill(self.ad(), make_ctx(config))
        c2 = [r for r in results if r.evidence.get("limb") == "C2"]
        assert c2, "C2 must fire below the stage floor with purchases on record"
        # return 0.75 < 0.35 × 5.64 = 1.974
        assert c2[0].evidence["recent_return"] == pytest.approx(0.75)

    def test_evidence_carries_implied_cpa(self, config):
        # §4 C2: the gate evaluates the return form but MUST report the
        # implied CPA (aov ÷ stage floor).
        results = kill(self.ad(), make_ctx(config))
        c2 = next(r for r in results if r.evidence.get("limb") == "C2")
        implied = c2.evidence["implied_cpa_max"]
        # trailing aov = 300/3 = 100; floor = 1.974 → implied ceiling ≈ 50.66
        assert implied == pytest.approx(100 / (0.35 * 5.64))
        assert c2.evidence["actual_cpa"] == pytest.approx(100.0)

    def test_not_above_floor(self, config):
        ad = make_ad(
            "c2", age_days=10,
            recent=Metrics(spend=200.0, purchases=2, purchase_value=2000.0, carts=2),
            trailing=Metrics(spend=400.0, carts=8, purchases=3, purchase_value=3000.0),
            lifetime=Metrics(spend=600.0, purchases=3),
        )
        assert kill(ad, make_ctx(config)) == []  # return 10 ≥ floor

    def test_scale_floor_is_higher(self, config):
        from moot.models import Stage

        ad = make_ad(
            "c2s", stage=Stage.SCALE, campaign_id="120000000000004", age_days=10,
            recent=Metrics(spend=200.0, purchases=2, purchase_value=500.0),
            trailing=Metrics(spend=400.0, carts=8, purchases=3, purchase_value=900.0),
            lifetime=Metrics(spend=600.0, purchases=3),
        )
        results = kill(ad, make_ctx(config))
        c2 = [r for r in results if r.evidence.get("limb") == "C2"]
        assert c2  # return 2.5 < scale floor 0.53 × 5.64 = 2.99
        assert c2[0].evidence["kill_floor"] == pytest.approx(0.53 * 5.64)


class TestLimbD:
    def test_fires_at_concept_level_zero_carts(self, config):
        # carts=0 is a RECORDED zero (§11.2) — the limb kills on it.
        dead_a = make_ad(
            "d1", market="US", post_id="p_dead", age_days=12,
            recent=Metrics(spend=30.0, outbound_clicks=60, carts=0),
            trailing=Metrics(spend=80.0, carts=10),
            lifetime=Metrics(spend=80.0),
        )
        dead_b = make_ad(
            "d2", market="US", post_id="p_dead", age_days=12,
            recent=Metrics(spend=30.0, outbound_clicks=60, carts=0),
            trailing=Metrics(spend=80.0, carts=10),
            lifetime=Metrics(spend=80.0),
        )
        results = kill(dead_a, make_ctx(config, market="US", ads=(dead_a, dead_b)))
        assert [r.evidence["limb"] for r in results] == ["D"]
        assert results[0].evidence["aggregate_outbound_clicks"] == 120
        assert dead_b.id in results[0].evidence["concept_ad_ids"]

    def test_absent_carts_everywhere_do_not_fire(self, config):
        """§11.2: absence is not evidence. A concept whose every ad reports
        carts as ABSENT (None, not zero) is unreadable on click quality —
        limb D must not synthesise the zero it kills on."""
        dead_a = make_ad(
            "d1", market="US", post_id="p_dead", age_days=12,
            recent=Metrics(spend=30.0, outbound_clicks=60),  # no carts key at all
            trailing=Metrics(spend=80.0, carts=10),
            lifetime=Metrics(spend=80.0),
        )
        dead_b = make_ad(
            "d2", market="US", post_id="p_dead", age_days=12,
            recent=Metrics(spend=30.0, outbound_clicks=60),
            trailing=Metrics(spend=80.0, carts=10),
            lifetime=Metrics(spend=80.0),
        )
        assert kill(dead_a, make_ctx(config, market="US", ads=(dead_a, dead_b))) == []

    def test_recorded_zero_plus_absent_still_aggregates(self, config):
        """Mixed evidence: one recorded zero is enough for the limb to read."""
        dead_a = make_ad(
            "d1", market="US", post_id="p_dead", age_days=12,
            recent=Metrics(spend=30.0, outbound_clicks=60, carts=0),
            trailing=Metrics(spend=80.0, carts=10),
            lifetime=Metrics(spend=80.0),
        )
        dead_b = make_ad(
            "d2", market="US", post_id="p_dead", age_days=12,
            recent=Metrics(spend=30.0, outbound_clicks=60),  # absent
            trailing=Metrics(spend=80.0, carts=10),
            lifetime=Metrics(spend=80.0),
        )
        results = kill(dead_a, make_ctx(config, market="US", ads=(dead_a, dead_b)))
        assert [r.evidence["limb"] for r in results] == ["D"]
        assert results[0].evidence["ads_without_recorded_carts"] == 1

    def test_fires_on_cart_rate_below_floor(self, config):
        # 150 clicks, 1 cart → 0.67% < 1.5% floor.
        dead = make_ad(
            "d1", market="US", post_id="p_dead", age_days=12,
            recent=Metrics(spend=60.0, outbound_clicks=150, carts=1),
            trailing=Metrics(spend=200.0, carts=4, purchases=1),
            lifetime=Metrics(spend=200.0, purchases=1),
        )
        results = kill(dead, make_ctx(config, market="US", ads=(dead,)))
        assert results and results[0].evidence["limb"] == "D"

    def test_not_below_click_threshold(self, config):
        dead = make_ad(
            "d1", market="US", post_id="p_dead", age_days=12,
            recent=Metrics(spend=30.0, outbound_clicks=99, carts=0),
            trailing=Metrics(spend=80.0, carts=10),
            lifetime=Metrics(spend=80.0),
        )
        assert kill(dead, make_ctx(config, market="US", ads=(dead,))) == []

    def test_healthy_cart_rate_does_not_fire(self, config):
        alive = make_ad(
            "d1", market="US", post_id="p_ok", age_days=12,
            recent=Metrics(spend=60.0, outbound_clicks=150, carts=6),  # 4% ≥ 1.5%
            trailing=Metrics(spend=200.0, carts=8, purchases=1),
            lifetime=Metrics(spend=200.0, purchases=1),
        )
        assert kill(alive, make_ctx(config, market="US", ads=(alive,))) == []


class TestStarved:
    def test_fires_under_max_spend(self, config):
        ad = make_ad(
            "st", age_days=6,
            recent=Metrics(spend=1.0),
            trailing=Metrics(spend=2.0),
            lifetime=Metrics(spend=2.0),
        )
        results = kill(ad, make_ctx(config))
        assert [r.evidence["limb"] for r in results] == ["starved"]

    def test_not_before_four_days(self, config):
        ad = make_ad("st", age_days=3, lifetime=Metrics(spend=1.0))
        assert kill(ad, make_ctx(config)) == []


class TestDeliveryFilter:
    def test_paused_ad_is_not_judged(self, config):
        ad = make_ad(
            "dk", status="PAUSED", age_days=20, trailing=Metrics(spend=500.0),
            lifetime=Metrics(spend=500.0),
        )
        assert kill(ad, make_ctx(config)) == []


class TestStaticHookImmunity:
    def test_static_ad_not_killed_on_missing_hook_rate(self, config):
        """§11.5: hook rate is undefined for static creative and NO kill limb
        may consume it. A static ad with absent video data and healthy
        numbers must not die."""
        static = make_ad(
            "s1", creative=CreativeType.STATIC, age_days=12,
            recent=Metrics(spend=200.0, purchases=2, purchase_value=1200.0, carts=20),
            trailing=Metrics(spend=300.0, carts=30, purchases=4, purchase_value=2000.0),
            lifetime=Metrics(spend=400.0, purchases=6),
        )
        assert static.recent.hook_rate is None
        assert static.trailing.hook_rate is None
        assert kill(static, make_ctx(config)) == []


class TestLimbDCartRateBand:
    """§3.2: limb D uses the market's computed cart-rate band (its low edge)
    when one exists, falling back to the configured floor only when the band
    is unavailable."""

    def _ctx(self, config, band, ads):
        baseline = MarketBaseline(
            market="NZ", value=10.0, source="computed", cart_rate_band=band
        )
        return make_ctx(config, market="NZ", ads=ads, baselines={"NZ": baseline})

    def _ad(self):
        return make_ad(
            "d1", market="NZ", post_id="p_band", age_days=12,
            recent=Metrics(spend=60.0, outbound_clicks=150, carts=3),
            trailing=Metrics(spend=200.0, carts=8, purchases=1),
            lifetime=Metrics(spend=200.0, purchases=1),
        )

    def test_band_low_used_when_available(self, config):
        # cart rate 0.02 is above the configured 0.015 floor but below the
        # band's low edge of 0.05 — the band must judge it dead.
        ad = self._ad()
        ctx = self._ctx(config, band=(0.05, 0.10), ads=(ad,))
        results = KillGate().evaluate(ad, ctx)
        assert [r.evidence["limb"] for r in results] == ["D"]
        assert results[0].evidence["cart_rate_floor"] == pytest.approx(0.05)
        assert results[0].evidence["cart_rate_floor_source"] == "computed_band_low"

    def test_configured_floor_used_without_band(self, config):
        # A fallback/seeded baseline carries no band: the configured floor
        # applies (0.02 ≥ 0.015 → healthy, no kill).
        ad = self._ad()
        ctx = self._ctx(config, band=None, ads=(ad,))
        assert KillGate().evaluate(ad, ctx) == []
