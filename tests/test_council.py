"""The council: contested marking, briefs, and the two mechanical vetoes."""

from __future__ import annotations

import pytest
from conftest import RUN_NOW

from moot.council import (
    ADJUDICATOR,
    AGENT_ROSTER,
    CONCENTRATION_THRESHOLD,
    DebateContext,
    brief,
    build_debate_context,
    charter_block,
    contested,
    hard_vetoes,
    post_concentration,
)
from moot.models import (
    Action,
    Ad,
    CreativeType,
    Decision,
    Metrics,
    Stage,
)


def pause(kill=True):
    return Action(
        verb="ad.pause", target_id="a1",
        source_gate=Decision.KILL if kill else Decision.FATIGUE,
    )


BUDGET_UP = Action(verb="campaign.budget_increase", target_id="c1",
                   source_gate=Decision.BUDGET_UP)
DUPLICATE = Action(verb="duplicate.post_id", target_id="a1",
                   source_gate=Decision.GRADUATE)


def make_context(**overrides) -> DebateContext:
    """A fully-numbered context: every figure the Round 0 brief promises."""
    defaults: dict = {
        "target": 5.64,
        "baseline": 12.0,
        "baseline_source": "computed",
        "baseline_population": 6,
        "stage": Stage.SCALE,
        "stage_spend": 800.0,
        "stage_return": 5.0,
        "concentration": 0.25,
        "recent_window_days": 7,
        "trailing_window_days": 30,
        "ad_name": "NZ-20260801-VID-scaled-out",
        "market": "NZ",
        "entity_id": "ad_1",
        "entity_kind": "ad",
        "recent": Metrics(spend=200.0, purchase_value=640.0, carts=16,
                          purchases=4, cpm=8.4, frequency=1.9,
                          outbound_ctr=0.0114),
        "trailing": Metrics(spend=600.0, purchase_value=3200.0, carts=60,
                            purchases=12, cpm=7.9, frequency=1.4,
                            outbound_ctr=0.013),
        "creative_type": CreativeType.VIDEO,
    }
    defaults.update(overrides)
    return DebateContext(**defaults)


class TestRoster:
    def test_five_archetypes_plus_adjudicator(self):
        assert len(AGENT_ROSTER) == 5
        assert ADJUDICATOR.id == "adjudicator"
        for archetype in AGENT_ROSTER:
            assert archetype.mandate and archetype.blind_spot
            assert archetype.primary_metrics
            assert archetype.id != ADJUDICATOR.id

    def test_hard_vetoes_declared_on_roster(self):
        vetoers = [a.id for a in AGENT_ROSTER if a.hard_veto]
        assert "risk-officer" in vetoers and "brand-steward" in vetoers

    def test_roster_ids_match_the_documented_charters(self):
        """council.py and plugin/agents/*.md must name the same five.

        They drifted once: the runtime invented "The Efficiency"/"The Growth"
        while the charters said Media Economist and Scaling Operator. A brief
        naming an archetype no charter file defines is unloadable.
        """
        assert {a.id for a in AGENT_ROSTER} == {
            "creative-architect",
            "media-economist",
            "scaling-operator",
            "risk-officer",
            "brand-steward",
        }


class TestContested:
    def test_clear_cut_kill_is_not_contested(self):
        """Standing opposition is not enough — the evidence must be close.

        The economist supports every kill and the architect opposes every kill,
        so opposition alone marks EVERY kill contested. An ad with zero carts on
        $200 of spend is not a matter of opinion; debating it wastes tokens and
        trains the reader to skim the ones that matter.
        """
        assert contested([pause()]) == []

    def test_borderline_kill_is_contested(self):
        """The same verb, with evidence saying the call is close."""
        close = Action(
            verb="ad.pause", target_id="ad_1", source_gate=Decision.KILL,
            evidence={"cost_per_cart": 17.5, "return_cpc_ceiling": 18.0},
        )
        results = contested([close])
        assert len(results) == 1
        assert "media-economist" in results[0].supporters
        assert "creative-architect" in results[0].opposers
        assert results[0].notes  # it must say WHY it is arguable

    def test_speculative_graduation_is_contested(self):
        spec = Action(
            verb="duplicate.post_id", target_id="ad_2",
            source_gate=Decision.GRADUATE, evidence={"speculative": True},
        )
        assert len(contested([spec])) == 1

    def test_concentrated_post_is_contested(self):
        concentrated = Action(
            verb="duplicate.post_id", target_id="ad_3",
            source_gate=Decision.GRADUATE, evidence={"revenue_share": 0.55},
        )
        assert len(contested([concentrated])) == 1

    def test_budget_up_is_contested(self):
        results = contested([BUDGET_UP])
        assert results
        assert "scaling-operator" in results[0].supporters
        assert ("media-economist" in results[0].opposers
                or "risk-officer" in results[0].opposers)

    def test_unopposed_action_not_contested(self):
        # Nothing in the roster argues for or against an unknown verb.
        neutral = Action(verb="mystery.verb", target_id="x")
        assert contested([neutral]) == []

    def test_opposition_requires_both_sides(self):
        results = contested([pause(), BUDGET_UP, DUPLICATE])
        for item in results:
            assert item.supporters and item.opposers


class TestBriefs:
    """Round 0: shared, role-neutral, and carrying every promised number.

    The old brief rendered no numbers at all and hardcoded a role — it
    contradicted docs/debate-protocol.md §2 ("Every agent receives exactly
    the same brief... No agent gets a fact the others do not") and its own
    calibration criterion ("Numbers appear in every argument"). These tests
    pin the corrected behaviour.
    """

    def test_brief_carries_the_action(self):
        text = brief(BUDGET_UP)
        assert "campaign.budget_increase" in text
        assert "c1" in text

    def test_brief_names_no_role(self):
        """The shared brief assigns no role — charters belong to Round 1."""
        text = brief(pause(), make_context())
        assert "You are the Adjudicator" not in text
        assert "Adjudicator" not in text
        for archetype in AGENT_ROSTER:
            assert archetype.title not in text
            assert archetype.id not in text
        assert ADJUDICATOR.mandate not in text

    def test_brief_without_context_states_numbers_unavailable(self):
        """A context-less brief must say so loudly — a brief with no numbers
        is a known failure mode, not a quiet degradation."""
        text = brief(pause())
        assert "NUMBERS UNAVAILABLE" in text
        assert "DebateContext" in text

    def test_brief_renders_baseline_source_target_windows_concentration(self):
        context = make_context()
        text = brief(pause(), context)
        assert "12.00" in text                       # baseline value
        assert "`computed`" in text                  # baseline_source
        assert "5.64" in text                        # the account target T
        assert "200.00" in text and "600.00" in text  # both windows' spend
        assert "25.00%" in text                      # concentration figure

    def test_brief_renders_gate_evidence_readably(self):
        action = Action(
            verb="ad.pause", target_id="a1", source_gate=Decision.KILL,
            evidence={"limb": "B", "cost_per_cart": 17.5},
        )
        text = brief(action, make_context())
        assert "limb: `'B'`" in text
        assert "cost_per_cart: `17.5`" in text
        assert str({"limb": "B", "cost_per_cart": 17.5}) not in text

    def test_brief_flags_concentration_above_threshold(self):
        text = brief(pause(), make_context(concentration=0.55))
        assert "55.00%" in text
        assert f"{CONCENTRATION_THRESHOLD:.0%} concentration threshold" in text

    def test_absent_metric_renders_em_dash_never_zero(self):
        """§11.2: absence is not zero — an absent metric prints "—", so a
        council never argues against a fabricated 0."""
        context = make_context(
            recent=Metrics(spend=200.0),   # everything else unreported
            trailing=None,                 # the whole window unreported
        )
        text = brief(pause(), context)
        assert "| Carts | — | — |" in text
        assert "| Purchases | — | — |" in text
        assert "| Return | — | — |" in text
        assert "| CPM | — | — |" in text

    def test_hook_rate_static_creative_is_marked_not_fabricated(self):
        """§11.5: hook rate is undefined for statics — the cell says why."""
        context = make_context(
            recent=Metrics(impressions=1000),
            trailing=Metrics(impressions=5000),
            creative_type=CreativeType.STATIC,
        )
        text = brief(pause(), context)
        assert "| Hook rate | n/a (static) | n/a (static) |" in text

    def test_brief_states_why_the_action_is_contested(self):
        concentrated = Action(
            verb="duplicate.post_id", target_id="ad_3",
            source_gate=Decision.GRADUATE, evidence={"revenue_share": 0.55},
        )
        text = brief(concentrated, make_context())
        assert "55% of stage revenue" in text


class TestCharterBlock:
    def test_full_charter_not_a_paraphrase(self):
        """§5: similar-sounding openings mean under-loaded charters — the
        block carries the whole mandate table."""
        block = charter_block(AGENT_ROSTER[0])
        for piece in ("Mandate", "Watches", "Always argues for",
                      "Always argues against", "Blind spot"):
            assert piece in block


class TestConcentration:
    """A post's share of its (market, stage) recent revenue — never 0."""

    @staticmethod
    def _ad(ad_id, post_id, value, *, stage=Stage.SCALE, market="NZ",
            status="ACTIVE"):
        recent = None if value is None else Metrics(purchase_value=value)
        return Ad(
            id=ad_id, name=ad_id, status=status, effective_status=status,
            market=market, stage=stage, post_id=post_id, recent=recent,
        )

    def test_post_share_of_stage_revenue(self):
        ads = [
            self._ad("a1", "p_big", 800.0),
            self._ad("a2", "p_small", 200.0),
        ]
        shares = post_concentration(ads)
        assert shares[("NZ", "SCALE", "p_big")] == pytest.approx(0.8)
        assert shares[("NZ", "SCALE", "p_small")] == pytest.approx(0.2)

    def test_shared_post_revenue_is_aggregated(self):
        ads = [
            self._ad("a1", "p_shared", 300.0),
            self._ad("a2", "p_shared", 300.0),
            self._ad("a3", "p_other", 300.0),
        ]
        shares = post_concentration(ads)
        assert shares[("NZ", "SCALE", "p_shared")] == pytest.approx(2 / 3)

    def test_none_when_stage_revenue_unrecorded(self):
        ads = [self._ad("a1", "p_x", None)]
        assert post_concentration(ads) == {}

    def test_dark_ads_excluded(self):
        """A paused ad's in-flight figures are not the stage's live revenue."""
        ads = [
            self._ad("a1", "p_live", 100.0),
            self._ad("a2", "p_dark", 900.0, status="PAUSED"),
        ]
        shares = post_concentration(ads)
        assert shares[("NZ", "SCALE", "p_live")] == pytest.approx(1.0)
        assert ("NZ", "SCALE", "p_dark") not in shares

    def test_markets_are_separate_auctions(self):
        ads = [
            self._ad("a1", "p_x", 100.0, market="NZ"),
            self._ad("a2", "p_x", 900.0, market="AU"),
        ]
        shares = post_concentration(ads)
        assert shares[("NZ", "SCALE", "p_x")] == pytest.approx(1.0)
        assert shares[("AU", "SCALE", "p_x")] == pytest.approx(1.0)


class TestBuildDebateContext:
    def test_builds_ad_context_from_run_state(self, config, adapter):
        from moot.pipeline import Pipeline

        run = Pipeline(adapter, config).run(now=RUN_NOW)
        action = next(
            a for a in run.actions
            if a.verb == "duplicate.post_id" and a.target_id == "ad_demote"
        )
        context = build_debate_context(
            action,
            baselines=run.baselines,
            campaigns=run.campaigns,
            adsets=run.adsets,
            ads=run.ads,
            config=run.config,
        )
        assert context.market == "NZ"
        assert context.stage is Stage.SCALE
        assert context.target == config.target
        assert context.baseline == run.baselines["NZ"].value
        assert context.baseline_source == "computed"
        assert context.recent is not None and context.recent.spend == 200.0
        assert context.trailing is not None and context.trailing.spend is not None
        # ad_demote is the only delivering SCALE/NZ ad → its post is 100%.
        assert context.concentration == pytest.approx(1.0)

    def test_builds_campaign_context_from_run_state(self, config, adapter):
        from moot.pipeline import Pipeline

        run = Pipeline(adapter, config).run(now=RUN_NOW)
        action = next(
            a for a in run.actions if a.verb == "campaign.budget_increase"
        )
        context = build_debate_context(
            action,
            baselines=run.baselines,
            campaigns=run.campaigns,
            adsets=run.adsets,
            ads=run.ads,
            config=run.config,
        )
        assert context.entity_kind == "campaign"
        assert context.market == "NZ"
        assert context.stage_spend == pytest.approx(800.0)
        assert context.stage_return == pytest.approx(5.0)

    def test_concentration_reaches_the_contested_marker(self, config, adapter):
        """The §1 row of the contested table — any action on a concentrated
        post — must actually fire: the pipeline stamps revenue_share into the
        action evidence from the same computation."""
        from moot.pipeline import Pipeline

        run = Pipeline(adapter, config).run(now=RUN_NOW)
        contested_actions = contested(run.actions + run.proposals)
        demote = [
            c for c in contested_actions
            if c.action.target_id == "ad_demote"
            and c.action.verb == "duplicate.post_id"
        ]
        assert demote
        assert "concentration" in demote[0].notes[0]


class TestHardVetoes:
    def test_risk_vetoes_guard_flagged_set(self, config):
        vetoes = hard_vetoes([pause()], guard_flagged=True, config=config)
        assert any(v["by"] == "risk" and v["scope"] == "all" for v in vetoes)

    def test_no_risk_veto_without_flag(self, config):
        assert hard_vetoes([pause()], guard_flagged=False, config=config) == []

    def test_brand_vetoes_forbidden_destination(self, config):
        action = Action(
            verb="ad.activate", target_id="a1",
            params={"destination_url": "https://shop.example.com/collections/all"},
        )
        vetoes = hard_vetoes([action], guard_flagged=False, config=config)
        assert any(v["by"] == "brand" for v in vetoes)

    def test_brand_vetoes_missing_required_pattern(self, config):
        action = Action(
            verb="ad.activate", target_id="a1",
            params={"destination_url": "https://shop.example.com/home"},
        )
        vetoes = hard_vetoes([action], guard_flagged=False, config=config)
        assert any("missing" in v["reason"] for v in vetoes)

    def test_compliant_destination_passes(self, config):
        # The example config sets require_tracking_params: true, so a
        # compliant action carries its utm_* taxonomy (§9 C) as well as a
        # policy-legal destination.
        action = Action(
            verb="ad.activate", target_id="a1",
            params={
                "destination_url": "https://shop.example.com/products/alpha",
                "url_tags": "utm_source=meta&utm_campaign=proving",
            },
        )
        assert hard_vetoes([action], guard_flagged=False, config=config) == []

    def test_brand_vetoes_missing_tracking_params(self, config):
        """require_tracking_params is enforced: a duplication without its
        utm_* taxonomy is invisible downstream (§9 C) and is vetoed."""
        action = Action(
            verb="duplicate.post_id", target_id="a1",
            params={"destination_url": "https://shop.example.com/products/alpha"},
        )
        vetoes = hard_vetoes([action], guard_flagged=False, config=config)
        assert any("tracking" in v["reason"] for v in vetoes)
