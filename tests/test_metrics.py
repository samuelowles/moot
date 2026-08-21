"""Metric parsing traps — docs/gates.md §11, every one a production incident."""

from __future__ import annotations

from agon.metrics import (
    extract_action,
    extract_action_value,
    parse_insights_row,
    to_float,
    to_int,
)


class TestCasting:
    def test_string_values_are_cast(self):
        row = {"spend": "123.45", "impressions": "3000", "cpm": "9.87"}
        metrics = parse_insights_row(row)
        assert metrics.spend == 123.45
        assert metrics.impressions == 3000
        assert metrics.cpm == 9.87

    def test_absent_spend_is_none_not_zero(self):
        metrics = parse_insights_row({})
        assert metrics.spend is None  # §11.2: absence, never a fabricated 0

    def test_empty_string_is_absent_not_zero(self):
        assert to_float("") is None
        assert to_int("") is None

    def test_real_zero_string_is_zero(self):
        assert to_float("0") == 0.0
        assert to_int("0") == 0

    def test_none_is_none(self):
        assert to_float(None) is None
        assert to_int(None) is None

    def test_garbage_is_none_never_raise(self):
        assert to_float("not-a-number") is None
        assert to_int("12.9") == 12


class TestSparseActions:
    def test_absent_purchase_key_yields_none_not_zero(self):
        # §11.2 — THE trap. A missing entry means absent from this response.
        metrics = parse_insights_row(
            {"actions": [{"action_type": "landing_page_view", "value": "40"}]}
        )
        assert metrics.purchases is None
        assert metrics.purchases != 0

    def test_omni_purchase_preferred_over_purchase(self):
        actions = [
            {"action_type": "purchase", "value": "1"},
            {"action_type": "omni_purchase", "value": "7"},
        ]
        assert extract_action(actions, ("omni_purchase", "purchase")) == 7

    def test_bare_purchase_fallback(self):
        actions = [{"action_type": "purchase", "value": "3"}]
        assert extract_action(actions, ("omni_purchase", "purchase")) == 3

    def test_omni_cart_fallback_chain(self):
        actions = [{"action_type": "add_to_cart", "value": "5"}]
        assert extract_action(actions, ("omni_add_to_cart", "add_to_cart")) == 5

    def test_value_from_matching_key(self):
        # §11.3 — value must come from the matching key of the value array.
        values = [
            {"action_type": "purchase_value", "value": "10.00"},
            {"action_type": "omni_purchase_value", "value": "90.00"},
        ]
        assert extract_action_value(values, ("omni_purchase_value", "purchase_value")) == 90.0

    def test_no_actions_array_at_all(self):
        metrics = parse_insights_row({"spend": "50"})
        assert metrics.purchases is None
        assert metrics.carts is None

    def test_zero_valued_action_is_a_real_zero(self):
        actions = [{"action_type": "omni_purchase", "value": "0"}]
        assert extract_action(actions, ("omni_purchase", "purchase")) == 0


class TestOutboundCtr:
    def test_decimal_fraction_not_rescaled(self):
        # §11.4: 0.0114 is 1.14%. The parser must not multiply or divide.
        metrics = parse_insights_row({"outbound_clicks_ctr": "0.0114"})
        assert metrics.outbound_ctr == 0.0114

    def test_percent_style_value_stays_untouched(self):
        metrics = parse_insights_row({"outbound_clicks_ctr": "1.4"})
        assert metrics.outbound_ctr == 1.4


class TestVideoFields:
    def test_static_creative_hook_rate_is_none(self):
        # §11.5: no video actions → hook_rate is None, never 0.0.
        metrics = parse_insights_row({"impressions": "10000"})
        assert metrics.hook_rate is None
        assert metrics.hook_rate != 0.0

    def test_video_hook_rate_computed(self):
        metrics = parse_insights_row({"impressions": "10000", "video_3s_views": "2500"})
        assert metrics.hook_rate == 0.25

    def test_zero_video_views_is_a_real_zero_not_absent(self):
        # _first_present must not let `0 or fallback` discard the zero.
        metrics = parse_insights_row(
            {"impressions": "10000", "video_3s_views": "0", "video_p25_watched_actions": "999"}
        )
        assert metrics.video_3s == 0
        assert metrics.hook_rate == 0.0
