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
        assert "risk" in vetoers and "brand" in vetoers


class TestContested:
    def test_kill_is_contested(self):
        # efficiency loves it, creative hates it — a real argument.
        results = contested([pause()])
        assert len(results) == 1
        assert "efficiency" in results[0].supporters
        assert "creative" in results[0].opposers

    def test_budget_up_is_contested(self):
        results = contested([BUDGET_UP])
        assert results
        assert "growth" in results[0].supporters
        assert "efficiency" in results[0].opposers or "risk" in results[0].opposers

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
        action = Action(
            verb="ad.activate", target_id="a1",
            params={"destination_url": "https://shop.example.com/products/alpha"},
        )
        assert hard_vetoes([action], guard_flagged=False, config=config) == []
