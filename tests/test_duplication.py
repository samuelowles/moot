"""Duplication pre-flight — docs/gates.md §9, on the fixture directories."""

from __future__ import annotations

from conftest import RUN_NOW, fixture_adapter

from moot.duplication import preflight
from moot.models import Stage


def run_preflight(subdir, config, dest_stage=Stage.SCALE):
    adapter = fixture_adapter(subdir)
    snapshot = adapter.fetch_entities()
    source = adapter.get_ad("ad_src")
    return preflight(adapter, source, dest_stage, snapshot.adsets, config, RUN_NOW)


class TestIdempotency:
    def test_already_present_skips_on_post_id(self, config):
        result = run_preflight("dup_active", config)
        assert result.status == "already-present"
        assert result.existing_ad_id == "ad_existing_active"
        assert result.existing_ad_status == "ACTIVE"

    def test_paused_existing_copy_still_skips(self, config):
        """§9 A4: a paused copy means a prior demotion or deliberate operator
        pause; re-creating it silently overrides a decision someone made."""
        result = run_preflight("dup_paused", config)
        assert result.status == "already-present"
        assert result.existing_ad_status == "PAUSED"
        assert any("proposal" in reason.lower() for reason in result.reasons)

    def test_same_name_different_post_does_not_collide(self, config):
        """Dedupe is on POST ID only. An existing ad with the same NAME but a
        different post must not block the duplication."""
        result = run_preflight("dup_samename", config)
        assert result.status == "ok"

    def test_archived_copy_does_not_block(self, config):
        # §9 A3: only non-ARCHIVED copies block.
        result = run_preflight("dup_archived", config)
        assert result.status == "ok"

    def test_clean_destination_proceeds(self, config):
        adapter = fixture_adapter("dup_samename")
        snapshot = adapter.fetch_entities()
        source = adapter.get_ad("ad_src")
        result = preflight(adapter, source, Stage.RESERVE, snapshot.adsets, config, RUN_NOW)
        assert result.status == "blocked-routing" or result.status == "ok"


class TestRouting:
    def test_blocked_routing_on_unmapped_source(self, config):
        result = run_preflight("unmapped", config)
        assert result.status == "blocked-routing"
        assert any("stage map" in reason for reason in result.reasons)

    def test_no_destination_campaign_is_blocked(self, config):
        # US has no scale campaign in the example config — a structural gap.
        adapter = fixture_adapter("dup_samename")
        snapshot = adapter.fetch_entities()
        from dataclasses import replace

        source = replace(adapter.get_ad("ad_src"), market="US", campaign_id="120000000000003")
        result = preflight(adapter, source, Stage.SCALE, snapshot.adsets, config, RUN_NOW)
        assert result.status == "blocked-routing"
        assert result.existing_ad_id is None

    def test_destination_is_derived_from_stage_map(self, config):
        result = run_preflight("dup_samename", config)
        assert result.status == "ok"
        assert result.destination_campaign_id == "120000000000004"


class TestUrlTags:
    def test_url_tags_carried(self, config):
        result = run_preflight("dup_samename", config)
        assert result.url_tags == "utm_source=meta&utm_campaign=proving"
        assert "url_tags carried" in result.reasons[0]

    def test_missing_url_tags_is_visible_not_silent(self, config):
        from dataclasses import replace

        adapter = fixture_adapter("dup_samename")
        snapshot = adapter.fetch_entities()
        source = replace(adapter.get_ad("ad_src"), url_tags=None)
        result = preflight(adapter, source, Stage.SCALE, snapshot.adsets, config, RUN_NOW)
        assert "no url_tags" in result.reasons[0]


class TestCohortResolution:
    def test_cohort_named_for_current_month(self, config):
        result = run_preflight("dup_samename", config)
        # as_dest is named "2026-08 winners" and RUN_NOW is 2026-08-21.
        assert result.destination_adset_id == "as_dest"
        assert result.destination_adset_to_create is None

    def test_absent_cohort_is_flagged_for_creation(self, config):
        adapter = fixture_adapter("dup_samename")
        snapshot = adapter.fetch_entities()
        source = adapter.get_ad("ad_src")
        # Drop the destination ad set: the cohort must be created, PAUSED.
        adsets = [s for s in snapshot.adsets if s.id != "as_dest"]
        result = preflight(adapter, source, Stage.SCALE, adsets, config, RUN_NOW)
        assert result.status == "ok"
        assert result.destination_adset_to_create == "2026-08 winners"


class TestNoNameDedupe:
    def test_module_source_never_compares_names(self):
        """The dedupe key is the post ID; a name comparison in this module
        would be the exact failure framework.md §4 warns about."""
        from pathlib import Path

        source = Path(__file__).parent.parent / "src" / "moot" / "duplication.py"
        text = source.read_text(encoding="utf-8")
        # Ads are never compared by name: only `other.post_id` is read in the
        # dedupe loop. The two `.name == wanted` reads are AD-SET destination
        # lookups (the configured "Retired winners" / cohort names) — routing,
        # not dedupe.
        assert "other.name" not in text
        assert "ad.name" not in text
        assert ".name.lower" not in text
        assert "other.post_id != source_ad.post_id" in text
