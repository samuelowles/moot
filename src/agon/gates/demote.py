"""DEMOTE — Scale → Reserve. docs/gates.md §7.

Four absolute conditions, no own-history decline requirement (unlike §6): a
scaled ad below the floor is demoted on its own numbers. The age condition is
not decoration — an ad judged before its attribution window closes reads far
worse than it is (one observed retarget ad read 0.30× on day three and
settled at 8.46×). The §7.1 auction check gates this retirement like every
other; the pipeline applies it before a DEMOTE survives.
"""

from __future__ import annotations

from agon.gates.base import GateContext, delivering
from agon.models import Ad, Decision, GateResult, Stage


class DemoteGate:
    """§7: spend floor, return floor, lifetime proof, minimum age."""

    name = "demote"

    def evaluate(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        if not isinstance(ad, Ad) or not delivering(ad):
            return []
        # §7 is the Scale → Reserve transition.
        if ad.stage is not Stage.SCALE:
            return []
        recent, lifetime = ad.recent, ad.lifetime
        floor = ctx.config.threshold("fatigue_return_floor")  # §7 uses 0.71 × T
        fatigue = ctx.config.fatigue  # min_spend/min_age defaults shared shape

        unmet: list[str] = []
        if recent is None or recent.spend is None or recent.spend < fatigue.min_spend:
            unmet.append(
                f"recent spend {None if recent is None else recent.spend} < "
                f"{fatigue.min_spend}"
            )
        ret = None if recent is None else recent.return_
        if ret is None or ret >= floor:
            unmet.append(f"return {ret} not below floor {floor:.2f}")
        if (
            lifetime is None
            or lifetime.purchases is None
            or lifetime.purchases < fatigue.min_lifetime_purchases
        ):
            unmet.append("lifetime purchases below minimum")
        if ad.age_days is None or ad.age_days < fatigue.min_age_days:
            unmet.append(f"age {ad.age_days} below {fatigue.min_age_days}d")
        if unmet:
            return []

        return [
            GateResult(
                decision=Decision.DEMOTE,
                entity_id=ad.id,
                reasons=[
                    f"DEMOTE — scaled ad below floor: recent spend "
                    f"{recent.spend} ≥ {fatigue.min_spend}, return {ret:.2f} < "
                    f"{floor:.2f}, {lifetime.purchases} lifetime purchases, age "
                    f"{ad.age_days}d ≥ {fatigue.min_age_days}d.",
                ],
                evidence={
                    "gate": "demote",
                    "recent_spend": recent.spend,
                    "recent_return": ret,
                    "floor": floor,
                    "lifetime_purchases": lifetime.purchases,
                    "age_days": ad.age_days,
                },
            )
        ]
