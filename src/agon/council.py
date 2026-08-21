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
        id="creative-architect",
        title="The Creative Architect",
        mandate="Creative is the targeting. The algorithm finds an audience; "
        "the creative decides which one it finds.",
        primary_metrics=("hook_rate", "hold_rate", "outbound_ctr",
                         "cost_per_cart", "days_since_graduation"),
        always_argues_for=("duplicate.post_id", "adset.create_cohort",
                           "ad.activate", "reserve.reactivate"),
        always_argues_against=("ad.pause@KILL",),
        blind_spot="Treats every loss as needing one more variant, so left "
        "alone it tests forever. Undervalues that a mediocre concept at scale "
        "outearns a brilliant one in test.",
    ),
    Archetype(
        id="media-economist",
        title="The Media Economist",
        mandate="Platform-reported return is a marketing claim, not a "
        "measurement. Only blended economics are real.",
        primary_metrics=("blended_mer", "marginal_return", "contribution_margin",
                         "data_density", "baseline"),
        always_argues_for=("ad.pause", "campaign.budget_decrease",
                           "ad.pause@KILL", "adset.pause"),
        always_argues_against=("campaign.budget_increase", "adset.create_cohort"),
        blind_spot="Starves the testing engine to protect the blend, and "
        "conflates statistical significance with commercial urgency. An "
        "account can be perfectly measured and quietly dying.",
    ),
    Archetype(
        id="scaling-operator",
        title="The Scaling Operator",
        mandate="Volume at target, not maximum efficiency. An under-scaled "
        "winner is a loss that appears on no dashboard.",
        primary_metrics=("marginal_return", "spend_velocity", "days_at_flat_budget",
                         "unharvested_graduates", "aov"),
        always_argues_for=("campaign.budget_increase", "duplicate.post_id",
                           "reserve.reactivate", "ad.activate"),
        always_argues_against=("campaign.budget_decrease", "adset.pause",
                               "campaign.pause"),
        blind_spot="Mistakes a ceiling for a plateau. Buys revenue at declining "
        "margin and calls it growth; will scale into fatigue and read the "
        "decline as an auction problem.",
    ),
    Archetype(
        id="risk-officer",
        title="The Risk Officer",
        mandate="The account is one confident, wrong run away from ruin. "
        "Assume the data is lying until proven otherwise.",
        primary_metrics=("paused_spend_share", "pull_completeness",
                         "concentration", "breaker_floor", "verification"),
        always_argues_for=(),
        always_argues_against=("duplicate.post_id", "campaign.budget_increase",
                               "adset.create_cohort"),
        hard_veto="anomaly_guard: any action set the anomaly guard flags is "
        "vetoed outright (§10)",
        blind_spot="Paralysis. Treats every anomaly as a fault and every "
        "unknown as a risk, forgetting that declining to act is also a "
        "decision with a cost — one that never appears in an audit log.",
    ),
    Archetype(
        id="brand-steward",
        title="The Brand Steward",
        mandate="Every impression is a deposit or a withdrawal. Performance "
        "borrowed against the brand comes due on someone else's dashboard.",
        primary_metrics=("frequency", "destination_url", "url_tags",
                         "cart_rate", "creative_repetition"),
        always_argues_for=(),
        always_argues_against=("ad.activate",),
        hard_veto="destination_policy: any action whose destination URL "
        "violates policy.destination is vetoed outright",
        blind_spot="Prices in brand equity nobody can measure and will veto "
        "profitable plays on taste. Its strongest arguments are its least "
        "falsifiable ones.",
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


#: Share of stage revenue above which a single post is treated as a
#: concentration risk, making any action on it worth arguing about
#: (docs/debate-protocol.md §1).
CONCENTRATION_THRESHOLD = 0.40


def _borderline(action: Action) -> Optional[str]:
    """Why this action is genuinely arguable, or None if it is clear-cut.

    Standing opposition between two archetypes is necessary but nowhere near
    sufficient. The efficiency lens supports every kill and the creative lens
    opposes every kill, so opposition alone marks *every* kill contested — and
    an ad with zero carts on $200 of spend is not a matter of opinion. Debating
    it wastes tokens and, worse, trains the reader to skim the ones that matter.

    So the evidence has to say the call is close. The cases below are exactly
    the contested list in docs/debate-protocol.md §1.
    """
    ev = action.evidence if isinstance(getattr(action, "evidence", None), dict) else {}
    gate = action.source_gate.value if action.source_gate is not None else ""

    if ev.get("speculative"):
        return "SPECULATIVE graduation — thin evidence by construction (§5)"
    if ev.get("path") == "B" or ev.get("graduate_path") == "B":
        return ("graduated on return rather than efficiency — expensive per "
                "cart but demonstrably earning (§5 Path B)")
    if _near_ceiling(ev):
        return ("cost per cart sits near the 1.80× baseline ceiling, where "
                "gates.md §5 requires a proposal rather than an execution")
    if gate in ("BUDGET_UP", "BUDGET_DOWN") or action.verb.startswith("campaign.budget"):
        return "a budget move — marginal versus average return is the whole argument (§8)"
    if ev.get("auction_close") or ev.get("auction_shift"):
        return "the auction-versus-fatigue check was close (§7.1)"
    share = ev.get("revenue_share")
    if isinstance(share, (int, float)) and share > CONCENTRATION_THRESHOLD:
        return (f"this post carries {share:.0%} of stage revenue — "
                "concentration is fragility (§1)")
    return None


def _near_ceiling(ev: dict[str, Any]) -> bool:
    """True when cost per cart is within 10% of the Path B ceiling."""
    cpc, ceiling = ev.get("cost_per_cart"), ev.get("return_cpc_ceiling")
    if not isinstance(cpc, (int, float)) or not isinstance(ceiling, (int, float)):
        return False
    return ceiling > 0 and cpc >= 0.90 * ceiling


def contested(actions: list[Action], roster: tuple[Archetype, ...] = AGENT_ROSTER
              ) -> list[ContestedAction]:
    """Mark every action the council would actually argue about.

    Two conditions, both required. At least two archetypes must score the
    action in opposite directions, AND the gate evidence must show the call is
    genuinely close (``_borderline``). Everything else executes on gates alone
    and appears in the report as uncontested.

    Calibration target is roughly one action in five (docs/debate-protocol.md
    §5). Far more means the gates are mistuned and the council is compensating
    for them, which is the wrong layer; far fewer means this filter has become
    too narrow and the council is decorative.
    """
    results: list[ContestedAction] = []
    for action in actions:
        supporters = tuple(a.id for a in roster if a.stance(action) == SUPPORT)
        opposers = tuple(a.id for a in roster if a.stance(action) == OPPOSE)
        if not (supporters and opposers):
            continue
        why = _borderline(action)
        if why is None:
            continue
        results.append(
            ContestedAction(
                action=action,
                supporters=supporters,
                opposers=opposers,
                notes=[why],
            )
        )
    return results


def hard_vetoes(
    actions: list[Action], guard_flagged: bool, config: Config
) -> list[dict[str, Any]]:
    """The two vetoes enforced mechanically, not by prose.

    1. The risk archetype vetoes any action set the anomaly guard flags.
    2. The brand archetype vetoes any action whose destination URL violates
       ``config.policy.destination`` — forbidden/required patterns, plus the
       ``require_tracking_params`` check: a duplication that carries no
       ``utm_*`` url_tags is invisible to every downstream analytics surface
       (§9 C) and is vetoed when the policy demands tracking parameters.
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
        reason = (
            f"destination {destination} violates policy "
            f"(forbidden: {violated}, missing: {missing})"
        )
        if policy.require_tracking_params:
            tags = action.params.get("url_tags")
            if not isinstance(tags, str) or "utm_" not in tags:
                reason += (
                    "; the action carries no utm_* url_tags while "
                    "policy.destination.require_tracking_params is on (§9 C)"
                )
                violated = violated or ["require_tracking_params"]
        if violated or missing:
            vetoes.append(
                {
                    "by": "brand",
                    "scope": action.target_id,
                    "reason": reason,
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
