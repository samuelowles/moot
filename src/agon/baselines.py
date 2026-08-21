"""Rolling per-market baselines — docs/gates.md §3, implemented exactly.

The baseline is the mean cost-per-cart of the top-quartile ad sets by return
in a market, recomputed every run before any gate evaluates. Top quartile,
not all ad sets: including losers inflates the bar until the gate stops
discriminating (framework.md §3.1).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from math import ceil
from typing import Any, Iterable, Optional

from agon.config import Config
from agon.models import AdSet, Stage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketBaseline:
    """The baseline for one market, with provenance (§3).

    ``source`` is one of:

    - ``"computed"`` — enough population; top-quartile mean of trailing
      cost-per-cart.
    - ``"fallback"`` — below ``baseline_min_population``; the market's
      configured ``baseline_fallback``.
    - ``"seeded"`` — below population with no fallback of its own; seeded
      from the configured analogue market (``seed_from``). A seeded market
      runs **kill gates only** — it cannot promote on a bar it has not yet
      earned (§3).
    """

    market: str
    value: Optional[float]
    source: str  # "computed" | "fallback" | "seeded"
    population: int = 0
    seeded_from: Optional[str] = None
    cart_rate_band: Optional[tuple[Optional[float], Optional[float]]] = None
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def kill_gates_only(self) -> bool:
        """True when the market must run kill gates only (§3: seeded)."""
        return self.source == "seeded"

    def describe(self) -> str:
        """One-line provenance for the run report (§3: state value AND source)."""
        seed = f", seeded from {self.seeded_from}" if self.seeded_from else ""
        return f"{self.market}: {self.value} ({self.source}, n={self.population}{seed})"


def _candidate(adset: AdSet, market: str, min_spend: float) -> bool:
    """§3 candidate filter: same market, PROVING/SCALE, spend floor, has carts.

    Reserve is excluded — its economics differ. Zero-cart ad sets are excluded
    because their cost-per-cart is undefined and would poison the mean.
    """
    if adset.market != market or adset.stage not in (Stage.PROVING, Stage.SCALE):
        return False
    trailing = adset.trailing
    if trailing is None or trailing.spend is None:
        return False
    if trailing.spend < min_spend:
        return False
    return trailing.carts is not None and trailing.carts > 0


def _top_quartile_slice(ranked: list[AdSet]) -> list[AdSet]:
    """The top ``max(1, ceil(n/4))`` entries of a return-ranked list (§3).

    ``max(1, ...)`` so a population of 1–4 still yields a baseline rather
    than an empty slice.
    """
    return ranked[: max(1, ceil(len(ranked) / 4))]


def compute_baseline(
    market: str,
    adsets: Iterable[AdSet],
    config: Config,
    seed_value: Optional[float] = None,
) -> MarketBaseline:
    """Compute one market's baseline and cart-rate band (§3, §3.2).

    ``seed_value`` is the analogue market's already-computed baseline, when
    one exists for this run; a seeded market falls back to the analogue's
    configured ``baseline_fallback`` otherwise.
    """
    min_population = config.baseline.min_population
    market_cfg = config.markets.get(market)
    candidates = [a for a in adsets if _candidate(a, market, config.baseline.min_spend)]

    if len(candidates) < min_population:
        # §3: a market with BOTH seed_from and baseline_fallback seeds — a
        # seeded market runs kill gates only, so it cannot graduate on a bar
        # it has not earned. The fallback would silently re-enable promotion.
        if market_cfg is not None and market_cfg.seed_from is not None:
            analogue = market_cfg.seed_from
            if seed_value is None:
                analogue_cfg = config.markets.get(analogue)
                seed_value = (
                    analogue_cfg.baseline_fallback if analogue_cfg is not None else None
                )
            return MarketBaseline(
                market=market,
                value=float(seed_value) if seed_value is not None else None,
                source="seeded",
                population=len(candidates),
                seeded_from=analogue,
                evidence={
                    "reason": f"population {len(candidates)} < {min_population}; "
                    f"seeded from analogue {analogue} (seed_from outranks a "
                    "configured baseline_fallback). Kill gates only this run "
                    "(docs/gates.md §3).",
                    "seed_value": seed_value,
                },
            )
        if market_cfg is not None and market_cfg.baseline_fallback is not None:
            return MarketBaseline(
                market=market,
                value=float(market_cfg.baseline_fallback),
                source="fallback",
                population=len(candidates),
                evidence={
                    "reason": f"population {len(candidates)} < {min_population}; "
                    "using configured baseline_fallback",
                },
            )
        # Structurally impossible after config validation (every market has a
        # fallback or a seed), but fail loudly rather than gate on a None.
        raise ValueError(
            f"market {market!r} has neither population, fallback nor seed — "
            "check config.markets"
        )

    # Rank by trailing return, descending; ad sets with undefined return sort
    # last — they have value data absent, not value zero (§11.2).
    ranked = sorted(
        candidates,
        key=lambda a: (a.trailing.return_ is not None, a.trailing.return_ or 0.0),
        reverse=True,
    )
    quartile = _top_quartile_slice(ranked)
    cpcs = [a.trailing.cost_per_cart for a in quartile]
    if not cpcs or any(c is None for c in cpcs):
        # cost_per_cart is None only when carts == 0, which the candidate
        # filter already excludes — guard anyway, never gate on a None.
        raise ValueError(f"market {market!r}: quartile slice has undefined cost_per_cart")
    mean_cpc = sum(cpcs) / len(cpcs)

    # §3.2 — cart-rate band across the same top quartile. None entries (an ad
    # set with no outbound clicks) stay None inside the band rather than 0.
    rates = [a.trailing.cart_rate for a in quartile]
    band_low = min((r for r in rates if r is not None), default=None)
    band_high = max((r for r in rates if r is not None), default=None)

    return MarketBaseline(
        market=market,
        value=mean_cpc,
        source="computed",
        population=len(candidates),
        cart_rate_band=(band_low, band_high),
        evidence={
            "quartile_size": len(quartile),
            "population": len(candidates),
            "quartile_adset_ids": [a.id for a in quartile],
            "quartile_cost_per_cart": cpcs,
        },
    )


def compute_baselines(adsets: Iterable[AdSet], config: Config) -> dict[str, MarketBaseline]:
    """Baselines for every configured market, ready before any gate runs (§3).

    Seeded markets are resolved after their analogues, so a seed inherits the
    analogue's live computed baseline rather than a stale configured fallback.
    """
    adsets = list(adsets)
    results: dict[str, MarketBaseline] = {}
    seeded: list[str] = []
    for market, market_cfg in config.markets.items():
        if market_cfg.seed_from is not None:
            seeded.append(market)
            continue
        results[market] = compute_baseline(market, adsets, config)
    for market in seeded:
        analogue = config.markets[market].seed_from
        seed_value = results[analogue].value if analogue in results else None
        results[market] = compute_baseline(market, adsets, config, seed_value=seed_value)
    for market, baseline in results.items():
        if baseline.source != "computed":
            logger.warning(
                "baseline for %s is %s (%s) — a fallback baseline silently in "
                "play is how gates rot (docs/gates.md §3)",
                market,
                baseline.value,
                baseline.source,
            )
    return results
