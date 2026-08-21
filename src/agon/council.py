"""The adversarial council — docs/framework.md §7.

The gates decide what is *eligible*; they cannot decide what is *wise*,
because every gate encodes a worldview and every worldview has a blind spot.
This module defines the roster of opposed mandates and marks the actions they
would contest. It does NOT call an LLM: stances here are mechanical keyword
positions, and the two HARD vetoes are enforced in code, not prose.

The debate itself — the briefs this module renders — is what external LLM
agents receive; the adjudicator descriptor frames their ruling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from agon.config import Config
from agon.models import Action

SUPPORT = 1
OPPOSE = -1


@dataclass(frozen=True)
class Archetype:
    """One councillor's mandate. ``stance(action)`` is a heuristic position,
    deliberately crude: the point is opposition, not precision."""

    id: str
    title: str
    mandate: str
    primary_metrics: tuple[str, ...]
    always_argues_for: tuple[str, ...]
    always_argues_against: tuple[str, ...]
    hard_veto: Optional[str] = None
    blind_spot: str = ""

    def stance(self, action: Action) -> int:
        """Deterministic position on one action: +1 for, −1 against, 0 neutral."""
        verb = action.verb
        if verb in self.always_argues_for:
            return SUPPORT
        if verb in self.always_argues_against:
            return OPPOSE
        # Source-gate nuance: e.g. the efficiency lens loves a kill but hates
        # the pause of a proven fatigued winner.
        gate = action.source_gate
        if verb in ("ad.pause",) and gate is not None:
            if f"{verb}@{gate.value}" in self.always_argues_for:
                return SUPPORT
            if f"{verb}@{gate.value}" in self.always_argues_against:
                return OPPOSE
        return 0


AGENT_ROSTER: tuple[Archetype, ...] = (
    Archetype(
        id="efficiency",
        title="The Efficiency",
        mandate="Cost per cart is the truth; waste dies young.",
        primary_metrics=("cost_per_cart", "return", "baseline"),
        always_argues_for=("ad.pause", "campaign.budget_decrease",
                           "ad.pause@KILL", "adset.pause"),
        always_argues_against=("campaign.budget_increase", "budget.bid_review"),
        blind_spot="Optimises the portfolio into a monoculture of safe winners; "
        "starves the testing engine that feeds it.",
    ),
    Archetype(
        id="creative",
        title="The Creative",
        mandate="Assets carry accumulated proof; protect option value.",
        primary_metrics=("hook_rate", "hold_rate", "outbound_ctr", "age_days"),
        always_argues_for=("duplicate.post_id", "adset.create_cohort",
                           "ad.activate", "reserve.reactivate"),
        always_argues_against=("ad.pause@KILL",),
        blind_spot="Falls in love with beautiful losers and spends forever on "
        "discovery at the portfolio's expense.",
    ),
    Archetype(
        id="growth",
        title="The Growth",
        mandate="Maximise spend subject to blended return near target.",
        primary_metrics=("return", "spend", "aov"),
        always_argues_for=("campaign.budget_increase", "duplicate.post_id",
                           "reserve.reactivate", "ad.activate"),
        always_argues_against=("campaign.budget_decrease", "adset.pause"),
        blind_spot="Buys revenue at declining margin; the blend can rot while "
        "spend climbs.",
    ),
    Archetype(
        id="risk",
        title="The Risk Officer",
        mandate="Assume the data is lying until proven otherwise.",
        primary_metrics=("paused_spend_share", "pull_completeness", "breaker_floor"),
        always_argues_for=(),
        always_argues_against=("duplicate.post_id", "campaign.budget_increase",
                               "adset.create_cohort"),
        hard_veto="anomaly_guard: any action set the anomaly guard flags is "
        "vetoed outright (§10)",
        blind_spot="Would freeze the account into stasis; a paused account has "
        "no risk and no return.",
    ),
    Archetype(
        id="brand",
        title="The Brand Guardian",
        mandate="Nothing ships to a page or taxonomy that betrays the catalogue.",
        primary_metrics=("cart_rate", "destination_url", "url_tags"),
        always_argues_for=(),
        always_argues_against=("ad.activate",),
        hard_veto="destination_policy: any action whose destination URL "
        "violates policy.destination is vetoed outright",
        blind_spot="Perfectionism: a landing page that never changes is a "
        "landing page nobody visits.",
    ),
)

ADJUDICATOR = Archetype(
    id="adjudicator",
    title="The Adjudicator",
    mandate="Weigh the council; rule on every contested action; own the "
    "outcome. Must rule, never abstain.",
    primary_metrics=("blended_return", "spend", "guard_verdicts"),
    always_argues_for=(),
    always_argues_against=(),
    blind_spot="Can only adjudicate the arguments placed before it — an angle "
    "no councillor raises is invisible to the ruling.",
)


@dataclass(frozen=True)
class ContestedAction:
    """An action at least two archetypes score in opposite directions."""

    action: Action
    supporters: tuple[str, ...]
    opposers: tuple[str, ...]
    notes: list[str] = field(default_factory=list)


def contested(actions: list[Action], roster: tuple[Archetype, ...] = AGENT_ROSTER
              ) -> list[ContestedAction]:
    """Mark every action the council would actually argue about.

    An action is contested when at least two archetypes score it in opposite
    directions — one SUPPORT and one OPPOSE. Uncontested actions pass
    straight through to dispatch.
    """
    results: list[ContestedAction] = []
    for action in actions:
        supporters = tuple(a.id for a in roster if a.stance(action) == SUPPORT)
        opposers = tuple(a.id for a in roster if a.stance(action) == OPPOSE)
        if supporters and opposers:
            results.append(
                ContestedAction(
                    action=action,
                    supporters=supporters,
                    opposers=opposers,
                )
            )
    return results


def hard_vetoes(
    actions: list[Action], guard_flagged: bool, config: Config
) -> list[dict[str, Any]]:
    """The two vetoes enforced mechanically, not by prose.

    1. The risk archetype vetoes any action set the anomaly guard flags.
    2. The brand archetype vetoes any action whose destination URL violates
       ``config.policy.destination``.
    """
    vetoes: list[dict[str, Any]] = []
    if guard_flagged:
        vetoes.append(
            {
                "by": "risk",
                "scope": "all",
                "reason": "anomaly guard flagged this run's action set — "
                "hard veto (§10)",
            }
        )
    policy = config.destination_policy
    for action in actions:
        destination = action.params.get("destination_url")
        if not isinstance(destination, str):
            continue
        violated = [
            pattern
            for pattern in policy.forbid_patterns
            if pattern and pattern in destination
        ]
        missing = [
            pattern
            for pattern in policy.require_patterns
            if pattern and pattern not in destination
        ]
        if violated or missing:
            vetoes.append(
                {
                    "by": "brand",
                    "scope": action.target_id,
                    "reason": (
                        f"destination {destination} violates policy "
                        f"(forbidden: {violated}, missing: {missing})"
                    ),
                }
            )
    return vetoes


def brief(action: Action) -> str:
    """Render the debate brief an LLM agent receives for one action."""
    lines = [
        "# Agon debate brief",
        "",
        "You are the Adjudicator. Rule on this action: execute, modify, or "
        "reject. You must rule.",
        "",
        f"**Action**: `{action.verb}` on `{action.target_id}`",
        f"**Source gate**: {action.source_gate.value if action.source_gate else 'none'}",
        f"**Rationale**: {action.rationale or '—'}",
        f"**Params**: `{action.params}`",
        "",
        "## Council positions",
    ]
    for archetype in AGENT_ROSTER:
        position = {SUPPORT: "FOR", OPPOSE: "AGAINST", 0: "neutral"}[archetype.stance(action)]
        lines.append(f"- **{archetype.title}** ({archetype.id}): {position} — {archetype.mandate}")
        lines.append(f"  - blind spot: {archetype.blind_spot}")
    lines += [
        "",
        "## Hard vetoes",
        "- Risk archetype vetoes any action set the anomaly guard flags (§10).",
        "- Brand archetype vetoes any action whose destination URL violates "
        "policy.destination.",
        "",
        "## Adjudicator frame",
        f"- {ADJUDICATOR.mandate}",
        f"- Adjudicator blind spot: {ADJUDICATOR.blind_spot}",
        "",
        "Rule with a one-line verdict and the single deciding reason.",
    ]
    return "\n".join(lines)
