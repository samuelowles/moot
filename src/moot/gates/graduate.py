"""GRADUATE — Proving Ground → Scale, by post ID. docs/gates.md §5.

Both paths require the volume gates. Path A is efficiency; Path B exists
because a cost-per-cart-only gate vetoes ads that are demonstrably printing
revenue. Hook rate is a VIDEO-ONLY volume gate and is *skipped entirely* for
static creative — never defaulted to zero (§5 note, §11.5): judging statics on
hook rate wipes out the best performers in most accounts.
"""

from __future__ import annotations

from typing import Any, Optional

from moot.config import GraduateGates
from moot.gates.base import GateContext, delivering
from moot.models import Ad, CreativeType, Decision, GateResult, Stage


class GraduateGate:
    """Volume gates + Path A, Path B, and the optional speculative path."""

    name = "graduate"

    def evaluate(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        """Volume gates then Paths A/B, then the speculative fallback."""
        if not isinstance(ad, Ad) or not delivering(ad):
            return []
        # §5 applies to the Proving Ground → Scale transition only.
        if ad.stage is not Stage.PROVING:
            return []
        # §3: a seeded market runs kill gates only — "a market cannot promote
        # on a bar it has not yet earned."
        if ctx.kill_only_market():
            return []

        # Paths A/B with the full volume gates come first; the speculative
        # path (§5: volume gates except purchases ≥ 2) is the fallback for
        # thin-evidence ads that full volume has not yet cleared.
        paths = self._paths(ad, ctx)
        if paths:
            return paths
        speculative = self._speculative(ad, ctx)
        return [speculative] if speculative is not None else []

    # --- volume gates -----------------------------------------------------------

    def _volume_failures(self, ad: Ad, ctx: GateContext, min_purchases: int) -> list[str]:
        """Shared volume gate checks (§5). Returns the list of failed checks —
        empty means the ad passed. Hook rate is checked for VIDEO only."""
        gates = ctx.config.graduate
        trailing = ad.trailing
        failures: list[str] = []
        if trailing is None:
            return ["no trailing metrics"]
        if trailing.spend is None or trailing.spend < gates.min_spend:
            failures.append(
                f"trailing spend {trailing.spend} < min_spend {gates.min_spend}"
            )
        if trailing.purchases is None or trailing.purchases < min_purchases:
            failures.append(
                f"trailing purchases {trailing.purchases} < min_purchases {min_purchases}"
            )
        # §5 note / §11.4: outbound_ctr is a decimal fraction — 0.0114 is 1.14%.
        if trailing.outbound_ctr is None or trailing.outbound_ctr < gates.min_ctr:
            failures.append(
                f"outbound CTR {trailing.outbound_ctr} < min_ctr {gates.min_ctr}"
            )
        # §5: hook_rate >= min_hook, VIDEO ONLY. Skipped entirely — not
        # defaulted to zero — for static and carousel creative.
        if ad.creative_type is CreativeType.VIDEO:
            hook = trailing.hook_rate
            if hook is None or hook < gates.min_hook:
                failures.append(
                    f"video hook rate {hook} < min_hook {gates.min_hook}"
                )
        return failures

    # --- Paths A and B ------------------------------------------------------------

    def _paths(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        """Path A (efficiency) or Path B (return), each behind §5's volume
        gates. Returns [] when neither path qualifies."""
        gates = ctx.config.graduate
        trailing = ad.trailing
        recent = ad.recent
        if trailing is None:
            return []
        failures = self._volume_failures(ad, ctx, gates.min_purchases)
        if failures:
            return []

        baseline = ctx.baseline().value
        if baseline is None or trailing.cost_per_cart is None:
            return []
        floor = ctx.config.threshold("graduate_return_floor")  # 0.71 × T (§2)

        evidence = {
            "path": None,
            "trailing_spend": trailing.spend,
            "trailing_purchases": trailing.purchases,
            "trailing_cost_per_cart": trailing.cost_per_cart,
            "baseline": baseline,
            "graduate_return_floor": floor,
            "recent_return": None if recent is None else recent.return_,
            "trailing_return": trailing.return_,
        }

        # Path A — efficiency: cost_per_cart(trailing) <= efficiency_max × baseline.
        if trailing.cost_per_cart <= gates.efficiency_max * baseline:
            return [
                GateResult(
                    decision=Decision.GRADUATE,
                    entity_id=ad.id,
                    reasons=[
                        f"GRADUATE Path A (efficiency): volume gates met; "
                        f"cost per cart {trailing.cost_per_cart:.2f} ≤ "
                        f"{gates.efficiency_max} × baseline "
                        f"({gates.efficiency_max * baseline:.2f}).",
                    ],
                    evidence={**evidence, "path": "A"},
                )
            ]
        return self._path_b(ad, gates, evidence, baseline, floor)

    def _path_b(
        self,
        ad: Ad,
        gates: GraduateGates,
        evidence: dict[str, Any],
        baseline: float,
        floor: float,
    ) -> list[GateResult]:
        """Path B — return: (recent OR trailing ≥ floor) AND cpc ≤ ceiling.

        §5 Path B note: an ad that qualifies on return while costing more
        than 1.80 × baseline per cart is a defensible trade — but it is
        *proposed, not executed*. Surface it as an unauthorized graduate.
        """
        trailing = ad.trailing
        recent = ad.recent
        if trailing is None:
            return []  # unreachable: _paths already checked; kept for the type checker
        cpc = trailing.cost_per_cart
        if cpc is None:
            return []  # unreachable for the same reason
        return_ok = (recent is not None and recent.return_ is not None and
                     recent.return_ >= floor) or (
            trailing.return_ is not None and trailing.return_ >= floor
        )
        ceiling = gates.return_cpc_ceiling * baseline
        if not return_ok:
            return []

        if cpc <= ceiling:
            return [
                GateResult(
                    decision=Decision.GRADUATE,
                    entity_id=ad.id,
                    reasons=[
                        f"GRADUATE Path B (return): return "
                        f"(recent {recent.return_ if recent else None} / trailing "
                        f"{trailing.return_}) ≥ floor {floor:.2f} while cost per "
                        f"cart {cpc:.2f} ≤ "
                        f"{gates.return_cpc_ceiling} × baseline ({ceiling:.2f}).",
                    ],
                    evidence={**evidence, "path": "B"},
                )
            ]
        return [
            GateResult(
                decision=Decision.GRADUATE,
                entity_id=ad.id,
                reasons=[
                    f"GRADUATE Path B (return), PROPOSED ONLY: return clears "
                    f"the floor but cost per cart {cpc:.2f} "
                    f"exceeds {gates.return_cpc_ceiling} × baseline "
                    f"({ceiling:.2f}) — anything past the ceiling is "
                    "proposed, not executed (§5).",
                ],
                evidence={**evidence, "path": "B", "proposed_only": True},
            )
        ]

    # --- speculative ----------------------------------------------------------------

    def _speculative(self, ad: Ad, ctx: GateContext) -> Optional[GateResult]:
        """§5 speculative: volume gates except purchases ≥ 2, and a high
        trailing return floor. Opt-in via ``gates.graduate.speculative_enabled``."""
        gates = ctx.config.graduate
        if not gates.speculative_enabled:
            return None
        trailing = ad.trailing
        if trailing is None:
            return None
        failures = self._volume_failures(ad, ctx, min_purchases=2)
        if failures:
            return None
        ret = trailing.return_
        if ret is None or ret < gates.speculative_return_min:
            return None
        return GateResult(
            decision=Decision.GRADUATE,
            entity_id=ad.id,
            reasons=[
                f"GRADUATE SPECULATIVE: volume gates met at purchases ≥ 2 and "
                f"trailing return {ret:.2f} ≥ {gates.speculative_return_min}. "
                "Flagged speculative — thin evidence, watched closely.",
            ],
            evidence={
                "path": "speculative",
                "speculative": True,
                "trailing_return": ret,
                "speculative_return_min": gates.speculative_return_min,
                "trailing_spend": trailing.spend,
                "trailing_purchases": trailing.purchases,
            },
        )
