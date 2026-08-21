"""KILL — terminal pause. docs/gates.md §4.

An ad is killed if ANY limb fires. Every limb requires the ad to be genuinely
delivering (§4 preamble): pausing an already-dark ad is a no-op the pipeline
filters out upstream, but the gate re-checks so it cannot fire out of band.

No limb consumes hook rate — hook quality is a *graduate* concern (§5); its
opposite failure mode, dead click quality, is limb D.
"""

from __future__ import annotations

from typing import Optional

from agon.gates.base import GateContext, delivering
from agon.models import Ad, Decision, GateResult, Stage

# §4 limb D: a concept's aggregated click count must be at least this thick
# before click quality is readable at all.
DEAD_CLICK_MIN_OUTBOUND = 100


def _recorded_zero(count: Optional[int]) -> bool:
    """True when a conversion count is zero, including absent-from-response.

    §11.2: action arrays are sparse — Meta omits zero-valued action types, so
    ``carts(trailing) is None`` on a completed row means *no carts recorded*,
    which for a ``== 0`` limb is a zero. The parse layer must never synthesise
    that zero (derived ratios would divide by it); the gate decides what
    absence means for its own limb, and here it means zero.
    """
    return count is None or count == 0


def _age_ok(ad: Ad, minimum_days: float) -> bool:
    """Age gate. Unknown age fails closed: never judge a window that has not
    had time to deliver (§7's age condition exists for exactly this reason)."""
    return ad.age_days is not None and ad.age_days >= minimum_days


def _spend_at_least(metrics_spend: Optional[float], floor: float) -> bool:
    """Spend floor. Unknown spend fails closed — spend is the evidence base."""
    return metrics_spend is not None and metrics_spend >= floor


class KillGate:
    """Limbs A, B, C1, C2, D (concept-level, per market) and the starved rule."""

    name = "kill"

    def evaluate(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        if not isinstance(ad, Ad) or not delivering(ad):
            return []
        fired = (
            self._limb_a(ad, ctx)
            + self._limb_b(ad, ctx)
            + self._limb_c1(ad, ctx)
            + self._limb_c2(ad, ctx)
            + self._starved(ad, ctx)
        )
        if fired:
            # Limb D pauses the whole concept at once; it is redundant evidence
            # when a per-ad limb already fired for this ad.
            return fired + self._limb_d(ad, ctx)
        return self._limb_d(ad, ctx)

    # --- A — never worked (trailing window) -----------------------------------

    def _limb_a(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        """§4 A: age ≥ 2d AND spend(trailing) ≥ a_min_spend AND carts == 0."""
        trailing = ad.trailing
        if trailing is None:
            return []
        min_spend = ctx.config.kill.a_min_spend
        if not _age_ok(ad, 2):
            return []
        if not _spend_at_least(trailing.spend, min_spend):
            return []
        if not _recorded_zero(trailing.carts):
            return []
        return [
            GateResult(
                decision=Decision.KILL,
                entity_id=ad.id,
                reasons=[
                    f"KILL A — never worked: age {ad.age_days}d ≥ 2d, trailing "
                    f"spend {trailing.spend} ≥ {min_spend}, zero carts on record.",
                ],
                evidence={
                    "limb": "A",
                    "age_days": ad.age_days,
                    "trailing_spend": trailing.spend,
                    "trailing_carts": trailing.carts,
                    "min_spend": min_spend,
                },
            )
        ]

    # --- B — expensive and unconverting (trailing window) ----------------------

    def _limb_b(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        """§4 B: age ≥ 2d AND spend(trailing) ≥ b_min_spend AND purchases == 0
        AND cost_per_cart(trailing) > cost_max × baseline."""
        trailing = ad.trailing
        if trailing is None:
            return []
        min_spend = ctx.config.kill.b_min_spend
        cost_max = ctx.config.kill.cost_max  # × baseline — §2 multiples table
        baseline = ctx.baseline().value
        if baseline is None or trailing.cost_per_cart is None:
            # Undefined cost_per_cart (no carts) or no baseline: B cannot
            # judge; the zero-conversion aspects belong to A and C1.
            return []
        if not _age_ok(ad, 2):
            return []
        if not _spend_at_least(trailing.spend, min_spend):
            return []
        if not _recorded_zero(trailing.purchases):
            return []
        ceiling = cost_max * baseline
        if not trailing.cost_per_cart > ceiling:
            return []
        return [
            GateResult(
                decision=Decision.KILL,
                entity_id=ad.id,
                reasons=[
                    f"KILL B — expensive and unconverting: age {ad.age_days}d ≥ 2d, "
                    f"trailing spend {trailing.spend} ≥ {min_spend}, zero purchases, "
                    f"cost per cart {trailing.cost_per_cart:.2f} > "
                    f"{cost_max} × baseline ({ceiling:.2f}).",
                ],
                evidence={
                    "limb": "B",
                    "age_days": ad.age_days,
                    "trailing_spend": trailing.spend,
                    "trailing_purchases": trailing.purchases,
                    "cost_per_cart": trailing.cost_per_cart,
                    "cost_max_multiple": cost_max,
                    "baseline": baseline,
                    "ceiling": ceiling,
                },
            )
        ]

    # --- C1 — recency: stalled (recent window) ---------------------------------

    def _limb_c1(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        """§4 C1: age ≥ recent window AND spend(recent) ≥ c_min_spend AND
        purchases(recent) == 0.

        C1 carries no lifetime-conversion condition by design — A and B's
        trailing zeros hand permanent immunity to an ad with one or two stale
        conversions (§4 C1/C2 rationale).
        """
        recent = ad.recent
        if recent is None:
            return []
        min_spend = ctx.config.kill.c_min_spend
        if not _age_ok(ad, ctx.config.windows.recent_days):
            return []
        if not _spend_at_least(recent.spend, min_spend):
            return []
        if not _recorded_zero(recent.purchases):
            return []
        return [
            GateResult(
                decision=Decision.KILL,
                entity_id=ad.id,
                reasons=[
                    f"KILL C1 — stalled: age {ad.age_days}d ≥ recent window "
                    f"({ctx.config.windows.recent_days}d), recent spend "
                    f"{recent.spend} ≥ {min_spend}, zero purchases.",
                ],
                evidence={
                    "limb": "C1",
                    "age_days": ad.age_days,
                    "recent_spend": recent.spend,
                    "recent_purchases": recent.purchases,
                    "min_spend": min_spend,
                },
            )
        ]

    # --- C2 — recency: uneconomic (recent window) -------------------------------

    def _limb_c2(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        """§4 C2: age ≥ recent window AND spend(recent) ≥ c_min_spend AND
        purchases(recent) ≥ 1 AND return(recent) < kill_floor[stage].

        This is the AOV-relative cost ceiling of framework.md §3.2 in its
        algebraic form: ``return < floor`` and ``CPA > aov / floor`` are the
        same statement. The runtime evaluates the return form and reports the
        implied CPA so the trade is legible in the audit (§4 C2).
        """
        recent = ad.recent
        if recent is None or recent.purchases is None:
            return []
        min_spend = ctx.config.kill.c_min_spend
        if not _age_ok(ad, ctx.config.windows.recent_days):
            return []
        if not _spend_at_least(recent.spend, min_spend):
            return []
        if recent.purchases < 1:
            return []
        stage = ad.stage or Stage.PROVING
        floor = ctx.config.kill_floor(stage)
        ret = recent.return_
        if ret is None or not ret < floor:
            return []
        # Implied CPA ceiling: the ad's own trailing AOV ÷ its stage's return
        # floor (framework.md §3.2). Fall back to recent AOV when trailing is
        # unreported; record which was used.
        aov = ad.trailing.aov if ad.trailing is not None else None
        aov_window = "trailing"
        if aov is None:
            aov = recent.aov
            aov_window = "recent"
        implied_cpa = aov / floor if aov is not None else None
        return [
            GateResult(
                decision=Decision.KILL,
                entity_id=ad.id,
                reasons=[
                    f"KILL C2 — uneconomic: age {ad.age_days}d ≥ recent window, "
                    f"recent spend {recent.spend} ≥ {min_spend}, "
                    f"{recent.purchases} purchase(s), return {ret:.2f} < "
                    f"{stage.value} kill floor {floor:.2f} "
                    f"(implied CPA ceiling "
                    f"{implied_cpa:.2f} vs actual {recent.cost_per_acquisition:.2f}).",
                ],
                evidence={
                    "limb": "C2",
                    "stage": stage.value,
                    "age_days": ad.age_days,
                    "recent_spend": recent.spend,
                    "recent_purchases": recent.purchases,
                    "recent_return": ret,
                    "kill_floor": floor,
                    "kill_floor_ratio": ctx.config.ratio(
                        f"kill_floor_{stage.value.lower()}"
                    ),
                    # §4 C2: evidence MUST carry the implied CPA even though
                    # the gate evaluates the return form.
                    "implied_cpa_max": implied_cpa,
                    "implied_cpa_aov_window": aov_window,
                    "actual_cpa": recent.cost_per_acquisition,
                },
            )
        ]

    # --- D — dead click quality (concept level, per market) ---------------------

    def _limb_d(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        """§4 D: aggregate every delivering ad carrying this concept (post ID)
        in this market, recent window.

        ``outbound_clicks ≥ 100 AND carts == 0`` OR
        ``outbound_clicks ≥ 100 AND cart_rate < cart_rate_floor``.

        Judged at concept level because the same creative runs across many ad
        sets and no single ad's click count is thick enough to read; judged
        per market because the same asset is routinely alive in one and dead
        in another. Independent of hook rate by design — high click-through
        with zero carts is the *more* damning signal (framework.md §5).

        §11.2: absence is not evidence. Only ads with a RECORDED value join
        each aggregate, and the limb requires at least one recorded cart
        figure — a concept whose every ad reports carts as absent (not zero)
        is unreadable on click quality, not dead on it.
        """
        if ad.post_id is None:
            return []
        concept_ads = [
            other
            for other in ctx.market_ads
            if other.post_id == ad.post_id and other.market == ad.market
        ]
        clicks = sum(
            a.recent.outbound_clicks
            for a in concept_ads
            if a.recent is not None and a.recent.outbound_clicks is not None
        )
        recorded_carts = [
            a.recent.carts
            for a in concept_ads
            if a.recent is not None and a.recent.carts is not None
        ]
        if not recorded_carts:
            # No ad in the concept recorded a cart count: absence is not the
            # zero this limb kills on.
            return []
        carts = sum(recorded_carts)
        if clicks < DEAD_CLICK_MIN_OUTBOUND:
            return []
        cart_rate = carts / clicks if clicks else None
        # §3.2: prefer the market's computed cart-rate band (its low edge —
        # below the worst healthy top-quartile cart rate is dead); fall back
        # to the configured floor when no band is available this run.
        band = ctx.baseline().cart_rate_band
        band_low = band[0] if band is not None else None
        floor = band_low if band_low is not None else ctx.config.kill.cart_rate_floor
        floor_source = "computed_band_low" if band_low is not None else "configured_floor"
        dead = carts == 0 or (cart_rate is not None and cart_rate < floor)
        if not dead:
            return []
        rate_text = "zero carts" if carts == 0 else f"cart rate {cart_rate:.4f} < {floor}"
        return [
            GateResult(
                decision=Decision.KILL,
                entity_id=ad.id,
                reasons=[
                    f"KILL D — dead click quality: concept {ad.post_id} in market "
                    f"{ad.market} bought {clicks} outbound clicks with {rate_text} "
                    f"(concept level, {len(concept_ads)} ad(s)).",
                ],
                evidence={
                    "limb": "D",
                    "concept_post_id": ad.post_id,
                    "market": ad.market,
                    "concept_ad_ids": [a.id for a in concept_ads],
                    "aggregate_outbound_clicks": clicks,
                    "aggregate_carts": carts,
                    "aggregate_cart_rate": cart_rate,
                    "cart_rate_floor": floor,
                    "cart_rate_floor_source": floor_source,
                    "ads_without_recorded_carts": len(concept_ads) - len(recorded_carts),
                    "min_outbound_clicks": DEAD_CLICK_MIN_OUTBOUND,
                },
            )
        ]

    # --- starved -----------------------------------------------------------------

    def _starved(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        """§4 starved: age ≥ 4d AND spend(lifetime) < starved_max_spend → pause.

        Not a performance judgement: delivery has concentrated elsewhere and
        the ad is occupying a slot without producing signal. Unknown lifetime
        spend fails closed — starvation is asserted by evidence, not silence.
        """
        lifetime = ad.lifetime
        if lifetime is None or lifetime.spend is None:
            return []
        max_spend = ctx.config.kill.starved_max_spend
        if not _age_ok(ad, 4):
            return []
        if not lifetime.spend < max_spend:
            return []
        return [
            GateResult(
                decision=Decision.KILL,
                entity_id=ad.id,
                reasons=[
                    f"KILL — starved: age {ad.age_days}d ≥ 4d but lifetime spend "
                    f"{lifetime.spend} < {max_spend}. Delivery concentrated "
                    "elsewhere; the slot produces no signal.",
                ],
                evidence={
                    "limb": "starved",
                    "age_days": ad.age_days,
                    "lifetime_spend": lifetime.spend,
                    "starved_max_spend": max_spend,
                },
            )
        ]
