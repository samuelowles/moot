"""GRADUATE paths — docs/gates.md §5, including the static hook skip."""

from __future__ import annotations

import pytest
from conftest import make_ad, make_ctx

from agon.gates.graduate import GraduateGate
from agon.models import CreativeType, Decision, Metrics


def graduate(ad, config, market="NZ", seeded=False):
    baselines = {}
    from agon.baselines import MarketBaseline

    baselines[market] = MarketBaseline(
        market=market, value=10.0, source="seeded" if seeded else "computed"
    )
    return GraduateGate().evaluate(ad, make_ctx(config, market=market, baselines=baselines))


VOLUME_OK = {
    "age_days": 12,
    "trailing": Metrics(
        spend=300.0, purchases=5, purchase_value=4500.0, carts=38,
        outbound_ctr=0.014, impressions=20000, video_3s=5000,
    ),
    "recent": Metrics(spend=250.0, purchases=3, purchase_value=1500.0, carts=30),
    "lifetime": Metrics(spend=600.0, purchases=8),
}


class TestPathA:
    def test_efficiency_path(self, config):
        ad = make_ad("g", creative=CreativeType.VIDEO, **VOLUME_OK)
        results = graduate(ad, config)
        assert len(results) == 1
        assert results[0].decision is Decision.GRADUATE
        assert results[0].evidence["path"] == "A"
        # cpc 300/38 ≈ 7.89 ≤ 0.85 × 10

    def test_volume_gate_blocks_low_spend(self, config):
        weak = dict(VOLUME_OK)
        weak["trailing"] = Metrics(
            spend=199.0, purchases=5, purchase_value=4500.0, carts=38,
            outbound_ctr=0.014, video_3s=5000,
        )
        assert graduate(make_ad("g", **weak), config) == []

    def test_volume_gate_blocks_low_purchases(self, config):
        weak = dict(VOLUME_OK)
        weak["trailing"] = Metrics(
            spend=300.0, purchases=2, purchase_value=1200.0, carts=38,
            outbound_ctr=0.014, video_3s=5000,
        )
        # return 4.0 < speculative floor 8.0, so only the purchases gate blocks.
        assert graduate(make_ad("g", **weak), config) == []

    def test_volume_gate_blocks_low_ctr(self, config):
        # 0.009 < 0.01 floor — and outbound_ctr is a fraction, not a percent (§11.4).
        weak = dict(VOLUME_OK)
        weak["trailing"] = Metrics(
            spend=300.0, purchases=5, purchase_value=4500.0, carts=38,
            outbound_ctr=0.009, video_3s=5000,
        )
        assert graduate(make_ad("g", **weak), config) == []


class TestPathB:
    def ad(self, cpc_carts):
        return make_ad(
            "gb", creative=CreativeType.VIDEO, age_days=12,
            trailing=Metrics(
                spend=300.0, purchases=5, purchase_value=4500.0, carts=cpc_carts,
                outbound_ctr=0.014, video_3s=5000, impressions=20000,
            ),
            recent=Metrics(spend=250.0, purchases=3, purchase_value=1300.0, carts=25),
            lifetime=Metrics(spend=600.0, purchases=8),
        )

    def test_return_path(self, config):
        # cpc = 300/17 ≈ 17.6 — above Path A's 8.5 but under the 1.80 × 10
        # ceiling, with recent return 5.2 ≥ 4.0 floor.
        results = graduate(self.ad(17), config)
        assert len(results) == 1 and results[0].evidence["path"] == "B"

    def test_past_ceiling_is_proposed_not_executed(self, config):
        # cpc = 300/10 = 30 > 1.80 × 10 = 18 while return clears the floor.
        results = graduate(self.ad(10), config)
        assert len(results) == 1
        assert results[0].evidence["proposed_only"] is True


class TestSpeculative:
    def test_speculative_when_enabled(self, config):
        ad = make_ad(
            "spec", creative=CreativeType.VIDEO, age_days=12,
            trailing=Metrics(
                spend=300.0, purchases=2, purchase_value=3000.0, carts=38,
                outbound_ctr=0.014, video_3s=5000, impressions=20000,
            ),
            recent=Metrics(spend=100.0),
            lifetime=Metrics(spend=400.0, purchases=2),
        )
        results = graduate(ad, config)
        assert len(results) == 1
        assert results[0].evidence["path"] == "speculative"
        assert results[0].evidence["speculative"] is True

    def test_speculative_off_blocks(self, tmp_path):
        import yaml
        from conftest import CONFIG_PATH

        from agon.config import load_config

        payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        payload["gates"]["graduate"]["speculative_enabled"] = False
        path = tmp_path / "c.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        no_spec_config = load_config(path)
        ad = make_ad(
            "spec", creative=CreativeType.VIDEO, age_days=12,
            trailing=Metrics(
                spend=300.0, purchases=2, purchase_value=3000.0, carts=38,
                outbound_ctr=0.014, video_3s=5000, impressions=20000,
            ),
            recent=Metrics(spend=100.0),
            lifetime=Metrics(spend=400.0, purchases=2),
        )
        assert graduate(ad, no_spec_config) == []


class TestHookGate:
    def test_hook_gate_skips_static_creative(self, config):
        """§5: hook rate is skipped entirely for static creative, not
        defaulted to zero. This static ad has no video data at all and still
        graduates."""
        static_kwargs = dict(VOLUME_OK)
        static_kwargs["trailing"] = Metrics(
            spend=300.0, purchases=5, purchase_value=4500.0, carts=38,
            outbound_ctr=0.014, impressions=20000,  # no video_3s
        )
        static = make_ad("s", creative=CreativeType.STATIC, **static_kwargs)
        assert static.trailing.hook_rate is None
        results = graduate(static, config)
        assert len(results) == 1 and results[0].evidence["path"] == "A"

    def test_video_with_weak_hook_fails_volume(self, config):
        weak = dict(VOLUME_OK)
        weak["trailing"] = Metrics(
            spend=300.0, purchases=5, purchase_value=4500.0, carts=38,
            outbound_ctr=0.014, impressions=20000, video_3s=3000,  # hook 0.15 < 0.20
        )
        ad = make_ad("v", creative=CreativeType.VIDEO, **weak)
        assert graduate(ad, config) == []

    def test_video_with_strong_hook_passes(self, config):
        ad = make_ad("v", creative=CreativeType.VIDEO, **VOLUME_OK)
        assert ad.trailing.hook_rate == pytest.approx(0.25)
        assert graduate(ad, config)


class TestScope:
    def test_scale_ad_is_not_graduated(self, config):
        from agon.models import Stage

        ad = make_ad(
            "sc", stage=Stage.SCALE, campaign_id="120000000000004",
            creative=CreativeType.VIDEO, **VOLUME_OK,
        )
        assert graduate(ad, config) == []

    def test_seeded_market_graduates_nothing(self, config):
        """§3: a seeded market runs kill gates only — no graduations."""
        ad = make_ad("s", creative=CreativeType.VIDEO, **VOLUME_OK)
        assert graduate(ad, config, seeded=True) == []
