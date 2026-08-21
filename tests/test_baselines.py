"""Baseline computation — docs/gates.md §3 and §3.2, exactly."""

from __future__ import annotations

import pytest

from agon.baselines import MarketBaseline, compute_baseline, compute_baselines
from agon.models import AdSet, Metrics, Stage


def adset(adset_id, market, stage, spend, value, carts, outbound=None):
    return AdSet(
        id=adset_id,
        name=adset_id,
        campaign_id="c1",
        market=market,
        stage=stage,
        trailing=Metrics(
            spend=spend, purchase_value=value, carts=carts, outbound_clicks=outbound
        ),
    )


FOUR_NZ = [
    adset("a", "NZ", Stage.PROVING, 1000, 9000, 100),  # return 9.0, cpc 10
    adset("b", "NZ", Stage.PROVING, 800, 5600, 40),    # return 7.0, cpc 20
    adset("c", "NZ", Stage.SCALE, 600, 3000, 30),      # return 5.0, cpc 20
    adset("d", "NZ", Stage.PROVING, 500, 1500, 25),    # return 3.0, cpc 20
]


class TestComputed:
    def test_population_four_takes_top_quartile_of_one(self, config):
        # ceil(4/4) = 1 → only the best-by-return ad set sets the bar.
        baseline = compute_baseline("NZ", FOUR_NZ, config)
        assert baseline.source == "computed"
        assert baseline.value == pytest.approx(10.0)  # the 9.0-return set's cpc

    def test_population_five_takes_two(self, config):
        five = FOUR_NZ + [adset("e", "NZ", Stage.PROVING, 400, 1000, 20)]
        baseline = compute_baseline("NZ", five, config)
        # ceil(5/4) = 2 → top two by return: cpc 10 and cpc 20 → mean 15.
        assert baseline.population == 5
        assert baseline.value == pytest.approx(15.0)
        assert baseline.evidence["quartile_size"] == 2

    def test_single_candidate_still_yields_baseline(self, config):
        one = [FOUR_NZ[0]]
        baseline = compute_baseline("NZ", one, config)
        assert baseline.source == "fallback"  # population 1 < 4

    def test_reserve_ad_sets_excluded(self, config):
        with_reserve = FOUR_NZ + [adset("r", "NZ", Stage.RESERVE, 5000, 50000, 10)]
        baseline = compute_baseline("NZ", with_reserve, config)
        assert baseline.population == 4  # the Reserve set is not a candidate

    def test_low_spend_excluded(self, config):
        thin = FOUR_NZ + [adset("t", "NZ", Stage.PROVING, 10, 100, 2)]
        baseline = compute_baseline("NZ", thin, config)
        assert baseline.population == 4  # under baseline_min_spend 100

    def test_zero_cart_ad_sets_excluded(self, config):
        no_carts = FOUR_NZ + [adset("z", "NZ", Stage.PROVING, 500, 5000, 0)]
        baseline = compute_baseline("NZ", no_carts, config)
        assert baseline.population == 4


class TestFallbackAndSeeded:
    def test_fallback_below_population(self, config):
        baseline = compute_baseline("NZ", FOUR_NZ[:2], config)
        assert baseline.source == "fallback"
        assert baseline.value == pytest.approx(24.90)  # config baseline_fallback

    def test_seeded_market_flags_kill_only(self, config, snapshot):
        baselines = compute_baselines(snapshot.adsets, config)
        us = baselines["US"]
        assert us.source == "seeded"
        assert us.kill_gates_only is True
        assert us.seeded_from == "AU"

    def test_seeded_inherits_analogue_computed_baseline(self, config, snapshot):
        baselines = compute_baselines(snapshot.adsets, config)
        # AU has population 4 → computed 12.0 (top set's cpc); US seeds from it.
        assert baselines["AU"].source == "computed"
        assert baselines["AU"].value == pytest.approx(12.0)
        assert baselines["US"].value == pytest.approx(12.0)

    def test_mature_market_not_kill_only(self, config, snapshot):
        baselines = compute_baselines(snapshot.adsets, config)
        assert baselines["NZ"].kill_gates_only is False
        assert baselines["NZ"].source == "computed"

    def test_describe_states_source(self, config, snapshot):
        baselines = compute_baselines(snapshot.adsets, config)
        text = baselines["US"].describe()
        assert "seeded" in text and "AU" in text


class TestCartRateBand:
    def test_band_across_top_quartile(self, config):
        # §3.2: min and max cart rate across the top-quartile sets.
        five = FOUR_NZ + [
            adset("e", "NZ", Stage.PROVING, 900, 8100, 45, outbound=450),
        ]
        for entry in five:
            if entry.id == "a":
                five[five.index(entry)] = adset(
                    "a", "NZ", Stage.PROVING, 1000, 9000, 100, outbound=2000)
        baseline = compute_baseline("NZ", five, config)
        # Top two by return: a (9.0) and e (9.0). Band across those two only.
        assert baseline.cart_rate_band is not None
        low, high = baseline.cart_rate_band
        assert low == pytest.approx(100 / 2000)
        assert high == pytest.approx(45 / 450)


class TestHandmade:
    def test_market_baseline_defaults(self):
        baseline = MarketBaseline(market="NZ", value=10.0, source="computed")
        assert baseline.kill_gates_only is False

    def test_market_baseline_seeded_flag(self):
        baseline = MarketBaseline(market="NZ", value=10.0, source="seeded", seeded_from="AU")
        assert baseline.kill_gates_only is True


class TestSeedOutranksFallback:
    """§3: a market with BOTH seed_from and baseline_fallback seeds — a
    seeded market runs kill gates only, so it cannot promote on a bar it has
    not earned; the fallback would silently re-enable promotion."""

    def test_both_configured_market_is_seeded(self, tmp_path, snapshot):
        import yaml
        from agon.config import load_config
        from conftest import CONFIG_PATH

        payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        payload["markets"]["US"]["baseline_fallback"] = 33.0
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        config = load_config(path)
        baselines = compute_baselines(snapshot.adsets, config)
        us = baselines["US"]
        assert us.source == "seeded"
        assert us.kill_gates_only is True
        # Seeded from AU's live computed baseline, not the 33.0 fallback.
        assert us.value == pytest.approx(baselines["AU"].value)
