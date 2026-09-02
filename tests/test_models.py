"""Model-layer contracts: derived ratios are None — never 0.0, never raised."""

from __future__ import annotations

import pytest

from moot.models import (
    Action,
    Ad,
    CreativeType,
    Decision,
    GateResult,
    Metrics,
    Stage,
)


class TestMetricsDerived:
    def test_return(self):
        assert Metrics(spend=100.0, purchase_value=450.0).return_ == 4.5

    def test_return_none_without_value(self):
        assert Metrics(spend=100.0).return_ is None

    def test_return_none_on_zero_spend(self):
        assert Metrics(spend=0.0, purchase_value=100.0).return_ is None

    def test_cost_per_cart(self):
        assert Metrics(spend=90.0, carts=3).cost_per_cart == 30.0

    def test_cost_per_cart_none_when_carts_absent(self):
        assert Metrics(spend=90.0).cost_per_cart is None

    def test_cpa_and_aov(self):
        m = Metrics(spend=200.0, purchases=2, purchase_value=150.0)
        assert m.cost_per_acquisition == 100.0
        assert m.aov == 75.0

    def test_cpa_none_on_zero_purchases(self):
        assert Metrics(spend=200.0, purchases=0).cost_per_acquisition is None

    def test_hold_rate(self):
        assert Metrics(video_3s=2500, thruplays=1000).hold_rate == 0.4

    def test_hold_rate_none_without_video(self):
        assert Metrics(thruplays=100).hold_rate is None

    def test_cart_rate(self):
        assert Metrics(carts=3, outbound_clicks=100).cart_rate == 0.03

    def test_cart_rate_none_on_zero_clicks(self):
        assert Metrics(carts=3, outbound_clicks=0).cart_rate is None


class TestEnums:
    def test_stage_from_config_key(self):
        assert Stage.from_config_key("proving") is Stage.PROVING
        assert Stage.from_config_key("retarget") is Stage.RETARGET

    def test_stage_unknown_raises(self):
        with pytest.raises(ValueError):
            Stage.from_config_key("beta")

    def test_creative_parse_variants(self):
        assert CreativeType.parse("VIDEO") is CreativeType.VIDEO
        assert CreativeType.parse("vid") is CreativeType.VIDEO
        assert CreativeType.parse("CAROUSEL") is CreativeType.CAROUSEL
        assert CreativeType.parse(None) is CreativeType.STATIC
        assert CreativeType.parse("mystery") is CreativeType.STATIC


class TestGateResultAndAction:
    def test_with_suppressed_keeps_evidence(self):
        result = GateResult(
            decision=Decision.FATIGUE, entity_id="a1",
            reasons=["r"], evidence={"x": 1},
        ).with_suppressed(Decision.KILL)
        assert result.suppressed_by is Decision.KILL
        assert result.evidence == {"x": 1}
        assert result.decision is Decision.FATIGUE

    def test_action_as_proposal_downgrades(self):
        action = Action(verb="ad.pause", target_id="a1", authorized=True)
        proposal = action.as_proposal("outside envelope")
        assert proposal.authorized is False
        assert action.authorized is True  # frozen original untouched

    def test_models_are_frozen(self):
        import dataclasses

        ad = Ad(id="a", name="n")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ad.name = "other"  # type: ignore[misc]
