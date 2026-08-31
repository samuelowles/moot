"""Shared gate plumbing: the evaluation context and the §7.1 auction check
that gates every retirement.

Each gate family module (kill/graduate/fatigue/demote/budget) implements the
docs/gates.md section named in its docstring: a ``name`` attribute and an
``evaluate(entity, ctx) -> list[GateResult]`` returning every result it
fires, in documented limb order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from agon.baselines import MarketBaseline
from agon.config import Config
from agon.models import Ad, Decision, GateResult, Metrics, Stage


@dataclass(frozen=True)
class GateContext:
    """Everything a gate may consult when judging one entity.

    One context per market per run: baselines are per-market (§3), kill limb D
    aggregates across the whole market (§4 D), and ``market_ads`` carries
    every genuinely-delivering ad in the market so concept-level limbs can
    read across ad sets.
    """

    config: Config
    baselines: dict[str, MarketBaseline]
    market: str
    now: datetime
    stage: Optional[Stage] = None
    market_ads: tuple[Ad, ...] = ()

    def baseline(self) -> MarketBaseline:
        """This market's baseline — never another market's (§3)."""
        return self.baselines[self.market]

    def kill_only_market(self) -> bool:
        """True when this market runs kill gates only (§3: seeded markets)."""
        return self.baseline().kill_gates_only


def delivering(entity: Ad) -> bool:
    """True only when the entity is genuinely delivering (§4 preamble).

    An ad whose parent ad set is paused matches many limbs, but pausing it is
    a no-op: the pipeline filters these out and reports them as already-dark.
    """
    return (entity.effective_status or "").upper() == "ACTIVE"


def auction_check(ad: Ad) -> Optional[GateResult]:
    """The §7.1 auction check — a hard gate on every retirement.

    Stable creative signal (CTR, and hook rate where defined) plus a
    materially risen CPM plus falling return means the auction got more
    expensive; the creative did not die. Retiring a healthy asset because the
    auction repriced is the most costly mistake available to this system, so
    this check converts a FATIGUE/DEMOTE into a budget-or-bid proposal.

    Returns ``None`` when the check does not fire — including when any input
    is unreported: an unverifiable auction shift cannot block a retirement.
    """
    recent, trailing = ad.recent, ad.trailing
    if recent is None or trailing is None:
        return None

    ctr_recent, ctr_trailing = recent.outbound_ctr, trailing.outbound_ctr
    ctr_ok = (
        ctr_recent is not None
        and ctr_trailing is not None
        and ctr_recent >= 0.90 * ctr_trailing  # §7.1
    )
    # Hook rate is skipped entirely where undefined (static creative) — §5, §11.5.
    hook_recent, hook_trailing = recent.hook_rate, trailing.hook_rate
    hook_ok = (
        hook_recent is None
        or hook_trailing is None
        or hook_recent >= 0.90 * hook_trailing  # §7.1
    )
    if not (ctr_ok and hook_ok):
        return None
    cpm_recent, cpm_trailing = recent.cpm, trailing.cpm
    if cpm_recent is None or cpm_trailing is None or not cpm_recent > 1.30 * cpm_trailing:
        return None  # §7.1
    ret_recent, ret_trailing = recent.return_, trailing.return_
    if ret_recent is None or ret_trailing is None or not ret_recent < ret_trailing:
        return None

    return GateResult(
        decision=Decision.BUDGET_UP,
        entity_id=ad.id,
        reasons=[
            "Auction check §7.1: creative signal stable while CPM rose "
            f"{cpm_recent / cpm_trailing:.2f}× and return fell — auction "
            "cost shift, not creative death. Retirement converted to a "
            "budget/bid proposal.",
        ],
        evidence={
            "gate": "auction_check",
            "auction_shift": True,
            "outbound_ctr_recent": ctr_recent,
            "outbound_ctr_trailing": ctr_trailing,
            "cpm_recent": cpm_recent,
            "cpm_trailing": cpm_trailing,
            "return_recent": ret_recent,
            "return_trailing": ret_trailing,
            "hook_rate_recent": hook_recent,
            "hook_rate_trailing": hook_trailing,
        },
    )


def window_evidence(
    recent: Optional[Metrics], trailing: Optional[Metrics]
) -> dict[str, Any]:
    """Standard evidence block so report/audit rows stay comparable across gates."""
    return {
        "recent_return": None if recent is None else recent.return_,
        "recent_spend": None if recent is None else recent.spend,
        "trailing_return": None if trailing is None else trailing.return_,
        "trailing_spend": None if trailing is None else trailing.spend,
    }
