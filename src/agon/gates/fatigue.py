"""FATIGUE — Proving Ground → Reserve. docs/gates.md §6.

A *proven* ad that decayed **against its own history** — condition 5 is what
separates fatigue from a bad week: an ad below the floor with no decline
against its own trailing baseline goes on the watchlist, not to the Reserve
(framework.md §5 "Decline against what?"). Every retirement is gated by the
§7.1 auction check, which the pipeline applies before this result survives.
"""

from __future__ import annotations

from agon.gates.base import GateContext, delivering
from agon.models import Ad, Decision, GateResult, Stage


def _conditions(ad: Ad, ctx: GateContext) -> tuple[list[str], dict]:
    """Evaluate §6 conditions 1–5. Returns (unmet, evidence).

    Conditions 1–4 are absolute floors; condition 5 compares the ad's recent
    window against the ad's OWN trailing window, never against the market.
    """
    fatigue = ctx.config.fatigue
    recent, trailing, lifetime = ad.recent, ad.trailing, ad.lifetime
    evidence: dict = {
        "recent_return": None if recent is None else recent.return_,
        "recent_spend": None if recent is None else recent.spend,
        "trailing_return": trailing.return_ if trailing else None,
        "trailing_cost_per_cart": trailing.cost_per_cart if trailing else None,
        "recent_cost_per_cart": recent.cost_per_cart if recent else None,
        "lifetime_purchases": None if lifetime is None else lifetime.purchases,
        "age_days": ad.age_days,
        "fatigue_return_floor": ctx.config.threshold("fatigue_return_floor"),
    }
    unmet: list[str] = []

    # 1. purchases(lifetime) >= fatigue_min_lifetime_purchases — "only proven
    #    creative enters the Reserve" (framework.md §2.1).
    if lifetime is None or lifetime.purchases is None or (
        lifetime.purchases < fatigue.min_lifetime_purchases
    ):
        unmet.append("1: lifetime purchases below minimum")
    # 2. age >= fatigue_min_age — judging before the attribution window closes
    #    reads far worse than the ad is (§7's observed 0.30× → 8.46× case).
    if ad.age_days is None or ad.age_days < fatigue.min_age_days:
        unmet.append("2: below minimum age")
    # 3. spend(recent) >= fatigue_min_spend.
    if recent is None or recent.spend is None or recent.spend < fatigue.min_spend:
        unmet.append("3: below recent spend floor")
    # 4. return(recent) < 0.71 × T.
    floor = ctx.config.threshold("fatigue_return_floor")
    ret = None if recent is None else recent.return_
    if ret is None or ret >= floor:
        unmet.append("4: return not below floor")

    # 5. Decline against the ad's OWN history — at least one of:
    #       cost_per_cart(recent) >= cost_ratio × cost_per_cart(trailing)
    #       return(recent)        <  return_ratio × return(trailing)
    decline_reasons: list[str] = []
    if (
        recent is not None
        and trailing is not None
        and recent.cost_per_cart is not None
        and trailing.cost_per_cart is not None
        and recent.cost_per_cart >= fatigue.cost_ratio * trailing.cost_per_cart
    ):
        decline_reasons.append(
            f"cost per cart rose to {fatigue.cost_ratio}× own trailing "
            f"({recent.cost_per_cart:.2f} vs {trailing.cost_per_cart:.2f})"
        )
    if (
        recent is not None
        and trailing is not None
        and recent.return_ is not None
        and trailing.return_ is not None
        and recent.return_ < fatigue.return_ratio * trailing.return_
    ):
        decline_reasons.append(
            f"return fell below {fatigue.return_ratio}× own trailing "
            f"({recent.return_:.2f} vs {trailing.return_:.2f})"
        )
    evidence["decline_reasons"] = decline_reasons
    return unmet, evidence


class FatigueGate:
    """§6 — fires FATIGUE only when all five conditions hold, including the
    condition-5 decline against the ad's own history."""

    name = "fatigue"

    def evaluate(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        """Fires only when all five §6 conditions hold."""
        if not isinstance(ad, Ad) or not delivering(ad):
            return []
        # §6 is the Proving Ground → Reserve transition.
        if ad.stage is not Stage.PROVING:
            return []
        unmet, evidence = _conditions(ad, ctx)
        # All five conditions: 1–4 unmet-free AND a decline of its own.
        if unmet or not evidence["decline_reasons"]:
            return []
        return [
            GateResult(
                decision=Decision.FATIGUE,
                entity_id=ad.id,
                reasons=[
                    "FATIGUE — proven ad decayed against its own history: "
                    + "; ".join(evidence["decline_reasons"])
                    + ".",
                ],
                evidence={**evidence, "gate": "fatigue"},
            )
        ]


class WatchGate:
    """The §6 watchlist limb: conditions 1–4 met, no decline of its own.

    Emits WATCH so the report's watchlist carries it. "An ad failing 1–4 with
    no decline against its own baseline goes on the watchlist, not to the
    Reserve."
    """

    name = "watch"

    def evaluate(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        """The §6 watchlist limb: conditions 1-4 met, no decline of its own."""
        if not isinstance(ad, Ad) or not delivering(ad):
            return []
        if ad.stage is not Stage.PROVING:
            return []
        unmet, evidence = _conditions(ad, ctx)
        # Below the floor (4 met), proven enough to judge (1–3 met), but no
        # decline against its own history (5 unmet) → watch, do not act.
        if unmet != [] or evidence["decline_reasons"]:
            return []
        return [
            GateResult(
                decision=Decision.WATCH,
                entity_id=ad.id,
                reasons=[
                    "WATCH — below the return floor without declining against "
                    "its own history: a bad week, not a terminal one (§6).",
                ],
                evidence={**evidence, "gate": "watch"},
            )
        ]
