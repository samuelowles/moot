"""Config loading: §2 derivation, overrides, validation, margin warning."""

from __future__ import annotations

import logging

import pytest
import yaml
from conftest import CONFIG_PATH

from agon.config import ConfigError, load_config
from agon.models import Stage


@pytest.fixture(scope="module")
def example_config():
    return load_config(CONFIG_PATH)


def write_config(tmp_path, payload) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return str(path)


def base_payload():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


class TestThresholdDerivation:
    def test_kill_floor_proving_derived_from_target(self):
        # §2: 0.35 × T. At T = 5.64 that is 1.974.
        assert load_config(CONFIG_PATH).threshold(
            "kill_floor", Stage.PROVING
        ) == pytest.approx(0.35 * 5.64)

    def test_kill_floor_scale_uses_higher_ratio(self):
        assert load_config(CONFIG_PATH).threshold(
            "kill_floor", Stage.SCALE
        ) == pytest.approx(0.53 * 5.64)

    def test_every_section2_threshold_scales_with_target(self, tmp_path):
        payload = base_payload()
        payload["target"] = 10.0
        config = load_config(write_config(tmp_path, payload))
        assert config.threshold("graduate_return_floor") == pytest.approx(7.1)
        assert config.threshold("fatigue_return_floor") == pytest.approx(7.1)
        assert config.threshold("budget_up_trigger") == pytest.approx(8.0)
        assert config.threshold("budget_down_trigger") == pytest.approx(5.3)
        assert config.threshold("breaker_floor") == pytest.approx(3.5)
        assert config.threshold("kill_floor", Stage.RESERVE) == pytest.approx(5.3)
        assert config.threshold("kill_floor", Stage.RETARGET) == pytest.approx(3.5)

    def test_threshold_override_via_gates_ratios(self, tmp_path):
        payload = base_payload()
        payload.setdefault("gates", {})["ratios"] = {"kill_floor_proving": 0.50}
        config = load_config(write_config(tmp_path, payload))
        assert config.threshold("kill_floor", Stage.PROVING) == pytest.approx(0.50 * 5.64)

    def test_breaker_override_via_guards_key(self, tmp_path):
        payload = base_payload()
        payload["guards"]["breaker_return_floor_ratio"] = 0.45
        config = load_config(write_config(tmp_path, payload))
        assert config.threshold("breaker_floor") == pytest.approx(0.45 * 5.64)

    def test_unknown_threshold_name_raises(self, example_config):
        with pytest.raises(ConfigError):
            example_config.threshold("mystery_floor")

    def test_kill_floor_requires_stage(self, example_config):
        with pytest.raises(ConfigError):
            example_config.threshold("kill_floor")


class TestValidation:
    def test_example_config_loads(self, example_config):
        assert example_config.account.allowed_account_ids == ("act_100000000000001",)

    def test_empty_allowed_account_ids_rejected(self, tmp_path):
        payload = base_payload()
        payload["account"]["allowed_account_ids"] = []
        with pytest.raises(ConfigError, match="allowed_account_ids"):
            load_config(write_config(tmp_path, payload))

    def test_missing_campaign_id_rejected(self, tmp_path):
        payload = base_payload()
        del payload["stages"]["proving"]["NZ"]["campaign_id"]
        with pytest.raises(ConfigError, match="campaign_id"):
            load_config(write_config(tmp_path, payload))

    def test_seed_from_unknown_market_rejected(self, tmp_path):
        payload = base_payload()
        payload["markets"]["US"] = {"seed_from": "XX"}
        with pytest.raises(ConfigError, match="seed_from"):
            load_config(write_config(tmp_path, payload))

    def test_missing_file_rejected(self):
        with pytest.raises(ConfigError, match="not found"):
            load_config("nowhere.yaml")

    def test_non_positive_target_rejected(self, tmp_path):
        payload = base_payload()
        payload["target"] = 0
        with pytest.raises(ConfigError, match="target"):
            load_config(write_config(tmp_path, payload))

    def test_unknown_gate_key_rejected(self, tmp_path):
        payload = base_payload()
        payload["gates"]["kill"]["a_min_spned"] = 30  # typo'd threshold
        with pytest.raises(ConfigError, match="unknown key"):
            load_config(write_config(tmp_path, payload))

    def test_stage_map_markets_must_exist(self, tmp_path):
        payload = base_payload()
        payload["stages"]["proving"]["DE"] = {"campaign_id": "1"}
        with pytest.raises(ConfigError, match="DE"):
            load_config(write_config(tmp_path, payload))


class TestMarginWarning:
    def test_no_warning_at_documented_defaults(self, caplog, tmp_path):
        # 65% margin ↔ 0.35 floor ratio: consistent per §2.
        with caplog.at_level(logging.WARNING):
            load_config(CONFIG_PATH)
        warnings = [r for r in caplog.records if "inconsistent with" in r.message]
        assert not warnings

    def test_warning_when_margin_inconsistent(self, caplog, tmp_path):
        payload = base_payload()
        payload["margin"]["gross_margin_pct"] = 40  # implied ratio 0.60 ≫ 0.35
        with caplog.at_level(logging.WARNING):
            load_config(write_config(tmp_path, payload))
        assert any("inconsistent with" in r.message for r in caplog.records)

    def test_warning_is_not_a_correction(self, caplog, tmp_path):
        payload = base_payload()
        payload["margin"]["gross_margin_pct"] = 40
        with caplog.at_level(logging.WARNING):
            config = load_config(write_config(tmp_path, payload))
        # The runtime warns; it never silently corrects (§2).
        assert config.ratio("kill_floor_proving") == 0.35


class TestStageMapLookups:
    def test_locate_campaign(self, example_config):
        entry = example_config.locate_campaign("120000000000001")
        assert entry is not None and entry.market == "NZ" and entry.stage is Stage.PROVING

    def test_locate_unknown_campaign(self, example_config):
        assert example_config.locate_campaign("nope") is None

    def test_us_scale_gap_is_none(self, example_config):
        assert example_config.destination_for(Stage.SCALE, "US") is None

    def test_envelope_forbidden_wins(self, example_config):
        assert not example_config.envelope.is_authorized("campaign.create")
        assert not example_config.envelope.is_authorized("delete")
        assert example_config.envelope.is_authorized("ad.pause")


class TestBreakerRatioPrecedence:
    """guards.breaker_return_floor_ratio wins only when the key is present;
    an explicit gates.ratios.breaker_floor override must survive an
    unconfigured guards block."""

    def test_ratios_breaker_floor_survives_without_guards_key(self, tmp_path):
        payload = base_payload()
        del payload["guards"]["breaker_return_floor_ratio"]
        payload["gates"]["ratios"] = {"breaker_floor": 0.50}
        config = load_config(write_config(tmp_path, payload))
        assert config.threshold("breaker_floor") == pytest.approx(0.50 * 5.64)

    def test_guards_key_wins_when_both_present(self, tmp_path):
        payload = base_payload()
        payload["gates"]["ratios"] = {"breaker_floor": 0.50}
        payload["guards"]["breaker_return_floor_ratio"] = 0.45
        config = load_config(write_config(tmp_path, payload))
        assert config.threshold("breaker_floor") == pytest.approx(0.45 * 5.64)


class TestDemoteGates:
    """§7 names demote_min_spend / demote_min_lifetime_purchases /
    demote_min_age — DEMOTE has its own keys, defaulting to the §6 values."""

    def test_demote_defaults_mirror_fatigue(self, tmp_path):
        payload = base_payload()
        del payload["gates"]["demote"]  # omit §7 keys → fatigue values apply
        payload["gates"]["fatigue"] = {"min_spend": 175.0, "min_age_days": 9,
                                       "min_lifetime_purchases": 4}
        config = load_config(write_config(tmp_path, payload))
        assert config.demote.min_spend == 175.0
        assert config.demote.min_age_days == 9
        assert config.demote.min_lifetime_purchases == 4

    def test_demote_keys_override_independently(self, tmp_path):
        payload = base_payload()
        payload["gates"]["demote"] = {"min_spend": 200}
        config = load_config(write_config(tmp_path, payload))
        assert config.demote.min_spend == 200
        assert config.demote.min_age_days == config.fatigue.min_age_days == 7

    def test_unknown_demote_key_rejected(self, tmp_path):
        payload = base_payload()
        payload["gates"]["demote"] = {"min_spned": 200}  # typo'd threshold
        with pytest.raises(ConfigError, match="gates.demote"):
            load_config(write_config(tmp_path, payload))

    def test_example_config_carries_demote_block(self, example_config):
        assert example_config.demote.min_spend == 150.0
        assert example_config.demote.min_lifetime_purchases == 3
        assert example_config.demote.min_age_days == 7


class TestPixelValidation:
    """docs/writes.md §4: the explicit pixel is a named safety property — a
    config without one refuses to load rather than post pixel_id=''."""

    def test_missing_pixel_id_rejected(self, tmp_path):
        payload = base_payload()
        del payload["pixel"]["id"]
        with pytest.raises(ConfigError, match="pixel"):
            load_config(write_config(tmp_path, payload))

    def test_empty_pixel_id_rejected(self, tmp_path):
        payload = base_payload()
        payload["pixel"]["id"] = ""
        with pytest.raises(ConfigError, match="pixel.id"):
            load_config(write_config(tmp_path, payload))

    def test_example_config_pixel_loads(self, example_config):
        assert example_config.pixel["id"] == "400000000000001"
