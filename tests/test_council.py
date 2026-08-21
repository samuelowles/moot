"""The council: contested marking, briefs, and the two mechanical vetoes."""

from __future__ import annotations

from agon.council import (
    ADJUDICATOR,
    AGENT_ROSTER,
    brief,
    contested,
    hard_vetoes,
)
from agon.models import Action, Decision


def pause(kill=True):
    return Action(
        verb="ad.pause", target_id="a1",
        source_gate=Decision.KILL if kill else Decision.FATIGUE,
    )


BUDGET_UP = Action(verb="campaign.budget_increase", target_id="c1",
                   source_gate=Decision.BUDGET_UP)
DUPLICATE = Action(verb="duplicate.post_id", target_id="a1",
                   source_gate=Decision.GRADUATE)


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
    def test_brief_renders_positions(self):
        text = brief(pause())
        assert "Adjudicator" in text
        assert "FOR" in text and "AGAINST" in text
        for archetype in AGENT_ROSTER:
            assert archetype.title in text

    def test_brief_carries_the_action(self):
        text = brief(BUDGET_UP)
        assert "campaign.budget_increase" in text
        assert "c1" in text


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
