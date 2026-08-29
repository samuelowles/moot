"""BUDGET — the only scaling lever. docs/gates.md §8.

Scale-stage campaign budgets only. Ad-set budgets are frozen to autonomous
increase in every stage: a qualifying ad set is *reported*, never raised —
Proving Ground budgets belong to the operator (framework.md §6). The +30%
single-step hard cap is enforced again in the write layer (writes.py); this
module caps the *proposed* step too so the report never shows an illegal ask.
"""

from __future__ import annotations

from typing import Any, Union

from agon.gates.base import GateContext
from agon.models import AdSet, Campaign, Decision, GateResult, Metrics, Stage

# §8: never exceed +30% in a single step — auction mechanics (a larger step
# resets the learning phase), not governance. Hard cap in code.
BUDGET_STEP_HARD_CAP_PCT = 30.0


def clamp_step_pct(requested_pct: float) -> float:
    """Clamp a requested budget-up step to the §8 hard cap, in code."""
    return min(requested_pct, BUDGET_STEP_HARD_CAP_PCT)


class BudgetGate:
    """§8 scale-up, scale-down, and reserve reactivation."""

    name = "budget"

    def evaluate(
        self, entity: Union[Campaign, AdSet], ctx: GateContext
    ) -> list[GateResult]:
        """§8 by entity kind: campaigns act, ad sets only report."""
        if isinstance(entity, Campaign):
            return self._campaign(entity, ctx)
        if isinstance(entity, AdSet):
            return self._adset(entity, ctx)
        return []

    def _campaign(self, campaign: Campaign, ctx: GateContext) -> list[GateResult]:
        # §8 applies to Scale-stage campaign budgets only. Reserve campaigns
        # get the reactivation limb; everything else is out of scope.
        if campaign.stage is Stage.SCALE:
            return self._scale_campaign(campaign, ctx)
        if campaign.stage is Stage.RESERVE:
            return self._reserve_campaign(campaign, ctx)
        return []

    def _scale_campaign(self, campaign: Campaign, ctx: GateContext) -> list[GateResult]:
        """§8 up and down limbs on one Scale campaign. Up is capped here so
        the report never shows an ask the write layer would refuse."""
        recent = campaign.recent
        if recent is None or recent.spend is None or recent.return_ is None:
            return []
        budget = ctx.config.budget
        ret = recent.return_
        evidence = {
            "campaign_id": campaign.id,
            "recent_spend": recent.spend,
            "recent_return": ret,
        }

        # Scale up: return >= budget_up_trigger (0.80 × T) AND spend >= up_min_spend.
        up_trigger = ctx.config.threshold("budget_up_trigger")  # §2
        if ret >= up_trigger and recent.spend >= budget.up_min_spend:
            step = clamp_step_pct(budget.step_pct)
            return [
                GateResult(
                    decision=Decision.BUDGET_UP,
                    entity_id=campaign.id,
                    reasons=[
                        f"BUDGET_UP: Scale campaign return {ret:.2f} ≥ "
                        f"{up_trigger:.2f} with recent spend {recent.spend} ≥ "
                        f"{budget.up_min_spend}. Increase by +{step:.0f}% "
                        f"(requested {budget.step_pct:.0f}%, §8 hard cap "
                        f"+{BUDGET_STEP_HARD_CAP_PCT:.0f}%).",
                    ],
                    evidence={
                        **evidence,
                        "gate": "budget",
                        "step_pct": step,
                        "requested_pct": budget.step_pct,
                        "up_trigger": up_trigger,
                    },
                )
            ]
        return self._scale_down(campaign, recent, evidence, ctx)

    def _scale_down(
        self,
        campaign: Campaign,
        recent: Metrics,
        evidence: dict[str, Any],
        ctx: GateContext,
    ) -> list[GateResult]:
        """Scale down: return < budget_down_trigger (0.53 × T) AND spend floor.

        Cutting a loser is not scaling — decreases stay authorised whatever
        the scale-up policy (§8)."""
        budget = ctx.config.budget
        down_trigger = ctx.config.threshold("budget_down_trigger")  # §2
        if recent.return_ is None or recent.spend is None:
            return []
        if not (recent.return_ < down_trigger and recent.spend >= budget.down_min_spend):
            return []
        return [
            GateResult(
                decision=Decision.BUDGET_DOWN,
                entity_id=campaign.id,
                reasons=[
                    f"BUDGET_DOWN: Scale campaign return {recent.return_:.2f} < "
                    f"{down_trigger:.2f} with recent spend {recent.spend} ≥ "
                    f"{budget.down_min_spend}. Decrease by "
                    f"−{budget.down_pct:.0f}%. Cutting a loser is not "
                    "scaling — decreases stay authorised whatever the "
                    "scale-up policy (§8).",
                ],
                evidence={
                    **evidence,
                    "gate": "budget",
                    "down_pct": budget.down_pct,
                    "down_trigger": down_trigger,
                },
            )
        ]

    def _reserve_campaign(self, campaign: Campaign, ctx: GateContext) -> list[GateResult]:
        """§8 reserve reactivation: campaign ACTIVE with trailing return at or
        above the up trigger → reactivate. The paused-ad-set condition is
        evaluated by the pipeline, which can see ad sets; this limb fires only
        on the campaign-level return condition and the pipeline attaches it to
        the paused ad set(s) it finds."""
        trailing = campaign.trailing
        up_trigger = ctx.config.threshold("budget_up_trigger")  # §2: 0.80 × T
        if trailing is None or trailing.return_ is None:
            return []
        if (campaign.effective_status or "").upper() != "ACTIVE":
            return []
        if trailing.return_ < up_trigger:
            return []
        return [
            GateResult(
                decision=Decision.RECOVER,
                entity_id=campaign.id,
                reasons=[
                    f"RECOVER candidate: Reserve campaign ACTIVE with trailing "
                    f"return {trailing.return_:.2f} ≥ {up_trigger:.2f}. "
                    "Reactivate the paused/under-delivering ad set (§8).",
                ],
                evidence={
                    "gate": "budget",
                    "limb": "reserve_reactivation",
                    "campaign_id": campaign.id,
                    "trailing_return": trailing.return_,
                    "up_trigger": up_trigger,
                },
            )
        ]

    def _adset(self, adset: AdSet, ctx: GateContext) -> list[GateResult]:
        """§8 hard rule: all ad-set budgets are frozen to autonomous increase.

        A qualifying ad set is reported (WATCH), never raised — there is no
        BUDGET_UP decision for an ad set under any condition.
        """
        recent = adset.recent
        if recent is None or recent.spend is None or recent.return_ is None:
            return []
        up_trigger = ctx.config.threshold("budget_up_trigger")
        if recent.return_ < up_trigger:
            return []
        return [
            GateResult(
                decision=Decision.WATCH,
                entity_id=adset.id,
                reasons=[
                    f"Ad set qualifies for a budget increase (return "
                    f"{recent.return_:.2f} ≥ {up_trigger:.2f}) but ad-set "
                    "budgets are frozen to autonomous increase in every stage "
                    "(§8). Reported to the operator, not raised.",
                ],
                evidence={
                    "gate": "budget",
                    "limb": "adset_frozen",
                    "adset_id": adset.id,
                    "recent_return": recent.return_,
                    "up_trigger": up_trigger,
                },
            )
        ]
