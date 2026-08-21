"""The run pipeline: filter → baseline → gates → precedence → actions → guards.

Implements the §12 precedence table exactly: exactly one transition per ad
per run, the losing gates recorded on the winner's ``suppressed_by`` so a
suppressed decision stays visible. Entities that are not genuinely delivering
are reported as already-dark rather than judged (§4 preamble).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Optional

from agon.adapters.base import AdPlatformAdapter, EntitySnapshot
from agon.baselines import MarketBaseline, compute_baselines
from agon.config import Config
from agon.duplication import preflight
from agon.gates.base import GateContext, auction_check
from agon.gates.budget import BudgetGate
from agon.gates.demote import DemoteGate
from agon.gates.fatigue import FatigueGate, WatchGate
from agon.gates.graduate import GraduateGate
from agon.gates.kill import KillGate
from agon.guards import GuardVerdict, RunSnapshot, evaluate_guards
from agon.models import (
    Action,
    Ad,
    AdSet,
    Campaign,
    Decision,
    GateResult,
    Stage,
)
from agon.writes import BUDGET_INCREASE_HARD_CAP_PCT

logger = logging.getLogger(__name__)

# §12 precedence for ad-level decisions, highest first. The auction check
# (rank 3) converts FATIGUE/DEMOTE into a budget proposal before this table
# is consulted; BUDGET actions are campaign-level and cannot collide per-ad.
AD_PRECEDENCE: tuple[Decision, ...] = (
    Decision.FATIGUE,
    Decision.KILL,
    Decision.DEMOTE,
    Decision.GRADUATE,
    Decision.BUDGET_UP,
    Decision.WATCH,
)

RETIREMENTS = (Decision.FATIGUE, Decision.DEMOTE)


@dataclass(frozen=True)
class AdResolution:
    """One ad's resolved transition plus the gates that lost to it (§12)."""

    ad: Ad
    winner: Optional[GateResult]
    losers: list[GateResult] = field(default_factory=list)


@dataclass(frozen=True)
class RunResult:
    """Everything one run computed, ready for report/writes/council."""

    config: Config
    snapshot: EntitySnapshot
    baselines: dict[str, MarketBaseline]
    resolutions: list[AdResolution]
    actions: list[Action]  # authorized, pending guards
    proposals: list[Action]  # downgraded before dispatch: routing, ceilings, policy
    watchlist: list[GateResult]
    already_dark: list[Ad]
    campaign_results: list[GateResult]
    guard: GuardVerdict
    daily_spend: dict[str, float]  # live daily spend by stage (§8 report duty)
    preflights: list[Any] = field(default_factory=list)


def _enrich(entity: Any, config: Config) -> Any:
    """Derive market/stage through the stage map when the platform did not.

    framework.md §4: routing facts are derived from the configured stage map,
    never inferred from names. Ads and ad sets carry their campaign id, which
    is the lookup key.
    """
    campaign_id = getattr(entity, "campaign_id", None)
    if campaign_id is None:
        return entity
    needs_stage = getattr(entity, "stage", None) is None
    needs_market = getattr(entity, "market", None) is None
    if not needs_stage and not needs_market:
        return entity
    entry = config.locate_campaign(campaign_id)
    if entry is None:
        return entity
    updates: dict[str, Any] = {}
    if needs_stage:
        updates["stage"] = entry.stage
    if needs_market:
        updates["market"] = entry.market
    return replace(entity, **updates)


def _active(entity: Any) -> bool:
    return (getattr(entity, "effective_status", None) or "").upper() == "ACTIVE"


def _account_return(snapshot: EntitySnapshot) -> Optional[float]:
    """Blended account return over the recent window (§10 breaker 1 input)."""
    value = spend = 0.0
    seen = False
    for campaign in snapshot.campaigns:
        if campaign.recent is None:
            continue
        if campaign.recent.purchase_value is not None:
            value += campaign.recent.purchase_value
            seen = True
        if campaign.recent.spend is not None:
            spend += campaign.recent.spend
    if not seen or spend <= 0:
        return None
    return value / spend


def _daily_spend(campaigns: list[Campaign], config: Config) -> dict[str, float]:
    """Live daily spend by stage — the §8 reporting obligation that replaced
    a spend cap. Recent spend spread across the recent window's days."""
    daily: dict[str, float] = {}
    days = max(config.windows.recent_days, 1)
    for campaign in campaigns:
        stage = campaign.stage.value if campaign.stage else "UNMAPPED"
        spend = campaign.recent.spend if campaign.recent else None
        if spend is None:
            continue
        daily[stage] = daily.get(stage, 0.0) + spend / days
    return daily


class Pipeline:
    """One autonomous run, computed end to end but never dispatched here."""

    def __init__(self, adapter: AdPlatformAdapter, config: Config):
        self.adapter = adapter
        self.config = config
        self.preflights: list[Any] = []
        self.kill = KillGate()
        self.graduate = GraduateGate()
        self.fatigue = FatigueGate()
        self.watch = WatchGate()
        self.demote = DemoteGate()
        self.budget = BudgetGate()

    # --- gate execution ---------------------------------------------------------

    def _ad_results(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        results: list[GateResult] = []
        for gate in (self.kill, self.graduate, self.fatigue, self.demote, self.watch):
            results.extend(gate.evaluate(ad, ctx))

        # §12 rank 3 — the auction check converts a retirement into a budget
        # proposal BEFORE precedence runs. It outranks FATIGUE/KILL because
        # retiring a repriced-but-healthy asset is the costliest mistake.
        if any(r.decision in RETIREMENTS for r in results):
            shift = auction_check(ad)
            if shift is not None:
                converted = [r.decision for r in results if r.decision in RETIREMENTS]
                results = [r for r in results if r.decision not in RETIREMENTS]
                shift = shift.with_suppressed(converted[0])
                results.append(shift)
        return results

    def _resolve(self, ad: Ad, results: list[GateResult]) -> AdResolution:
        """§12: exactly one transition; losers recorded on the winner.

        The auction check is rank 3 — above FATIGUE and KILL — so an
        auction-converted result always outranks everything else.
        """
        if not results:
            return AdResolution(ad=ad, winner=None)

        def rank(result: GateResult) -> tuple[int, int]:
            auction = 0 if result.evidence.get("auction_shift") else 1
            decision_rank = (
                AD_PRECEDENCE.index(result.decision)
                if result.decision in AD_PRECEDENCE
                else len(AD_PRECEDENCE)
            )
            return (auction, decision_rank)

        ranked = sorted(results, key=rank)
        winner = ranked[0]
        losers = ranked[1:]
        if losers:
            winner = winner.with_suppressed(losers[0].decision)
        return AdResolution(ad=ad, winner=winner, losers=losers)

    # --- actions -------------------------------------------------------------------

    def _pause_action(self, ad: Ad, result: GateResult, rationale: str) -> Action:
        return Action(
            verb="ad.pause",
            target_id=ad.id,
            params={"entity_type": "ad", "status": "PAUSED", "ad_name": ad.name},
            authorized=self.config.envelope.is_authorized("ad.pause"),
            rationale=rationale,
            source_gate=result.decision,
        )

    def _retirement_actions(
        self, ad: Ad, result: GateResult, now: datetime, adsets: list[AdSet]
    ) -> tuple[list[Action], list[Action]]:
        """§6/§7 action: duplicate into Reserve, then pause the source — but
        only after the copy verifies. Blocked routing pauses nothing: retiring
        without preserving the post ID destroys the asset (framework.md §4)."""
        checks = preflight(
            self.adapter, ad, Stage.RESERVE, adsets, self.config, now
        )
        self.preflights.append(checks)
        if checks.status == "ok":
            actions: list[Action] = []
            if checks.destination_adset_to_create:
                actions.append(
                    Action(
                        verb="adset.create_cohort",
                        target_id=checks.destination_campaign_id or "",
                        params={
                            "name": checks.destination_adset_to_create,
                            "campaign_id": checks.destination_campaign_id,
                            "pixel_id": self.config.pixel.get("id", ""),
                            "born": "PAUSED",
                        },
                        authorized=self.config.envelope.is_authorized("adset.create_cohort"),
                        rationale="destination ad set absent — created PAUSED (§5 action)",
                        source_gate=result.decision,
                    )
                )
            actions.append(
                Action(
                    verb="duplicate.post_id",
                    target_id=ad.id,
                    params={
                        "destination_campaign_id": checks.destination_campaign_id,
                        "destination_adset_id": checks.destination_adset_id,
                        "destination_adset_name": checks.destination_adset_to_create,
                        "destination_stage": Stage.RESERVE.value,
                        "post_id": ad.post_id,
                        "url_tags": ad.url_tags,
                        "page_id": (
                            self.config.markets.get(ad.market or "").destination_page_id
                            if ad.market
                            else None
                        ),
                    },
                    authorized=self.config.envelope.is_authorized("duplicate.post_id"),
                    rationale=result.reasons[0] if result.reasons else "retirement",
                    source_gate=result.decision,
                )
            )
            # §6: pause the source AFTER the copy verifies ACTIVE. The write
            # layer sequences this; here the intent is recorded in order.
            actions.append(
                self._pause_action(
                    ad, result,
                    f"{result.decision.value}: pause source after the Reserve "
                    "copy verifies (§6/§7 action).",
                )
            )
            return actions, []
        if checks.status == "already-present":
            # §6: the retirement is complete, only the copy was redundant.
            return (
                [self._pause_action(
                    ad, result,
                    f"{result.decision.value}: post already in the Reserve — "
                    "copy redundant, retirement complete (§6).",
                )],
                [],
            )
        # blocked-routing: propose only; do not pause the source.
        return (
            [],
            [
                Action(
                    verb="duplicate.post_id",
                    target_id=ad.id,
                    params={"blocked": checks.status, "post_id": ad.post_id},
                    authorized=False,
                    rationale="; ".join(checks.reasons),
                    source_gate=result.decision,
                )
            ],
        )

    def _graduate_actions(
        self, ad: Ad, result: GateResult, now: datetime, adsets: list[AdSet]
    ) -> tuple[list[Action], list[Action]]:
        """§5 action: §9 pre-flight, duplicate into the destination market's
        current month cohort ad set, born PAUSED. Do NOT pause the source —
        the original keeps harvesting its own audience (framework.md §2.1)."""
        checks = preflight(self.adapter, ad, Stage.SCALE, adsets, self.config, now)
        self.preflights.append(checks)
        proposed_only = bool(result.evidence.get("proposed_only"))
        if checks.status == "ok":
            actions: list[Action] = []
            if checks.destination_adset_to_create:
                actions.append(
                    Action(
                        verb="adset.create_cohort",
                        target_id=checks.destination_campaign_id or "",
                        params={
                            "name": checks.destination_adset_to_create,
                            "campaign_id": checks.destination_campaign_id,
                            "pixel_id": self.config.pixel.get("id", ""),
                            "born": "PAUSED",
                        },
                        authorized=self.config.envelope.is_authorized("adset.create_cohort"),
                        rationale="cohort ad set absent — created PAUSED (§5 action)",
                        source_gate=Decision.GRADUATE,
                    )
                )
            actions.append(
                Action(
                    verb="duplicate.post_id",
                    target_id=ad.id,
                    params={
                        "destination_campaign_id": checks.destination_campaign_id,
                        "destination_adset_id": checks.destination_adset_id,
                        "destination_adset_name": checks.destination_adset_to_create,
                        "destination_stage": Stage.SCALE.value,
                        "post_id": ad.post_id,
                        "url_tags": ad.url_tags,
                        "page_id": (
                            self.config.markets.get(ad.market or "").destination_page_id
                            if ad.market
                            else None
                        ),
                        "activate_after_verify": True,
                    },
                    authorized=self.config.envelope.is_authorized("duplicate.post_id"),
                    rationale=result.reasons[0] if result.reasons else "graduation",
                    source_gate=Decision.GRADUATE,
                )
            )
            if proposed_only:
                # §5 Path B note: past the 1.80 × baseline ceiling a graduation
                # is proposed, not executed.
                actions = [a.as_proposal("proposed only (§5 Path B ceiling)") for a in actions]
            return actions, []
        if checks.status == "already-present":
            return [], []
        return (
            [],
            [
                Action(
                    verb="duplicate.post_id",
                    target_id=ad.id,
                    params={"blocked": checks.status, "post_id": ad.post_id},
                    authorized=False,
                    rationale="; ".join(checks.reasons),
                    source_gate=Decision.GRADUATE,
                )
            ],
        )

    def _auction_actions(self, ad: Ad, result: GateResult) -> tuple[list[Action], list[Action]]:
        """§7.1: a budget-or-bid PROPOSAL, never an executed write on its own."""
        return (
            [],
            [
                Action(
                    verb="budget.bid_review",
                    target_id=ad.id,
                    params={
                        "auction_shift": True,
                        "cpm_recent": result.evidence.get("cpm_recent"),
                        "cpm_trailing": result.evidence.get("cpm_trailing"),
                    },
                    authorized=False,
                    rationale=result.reasons[0],
                    source_gate=Decision.BUDGET_UP,
                )
            ],
        )

    def _campaign_actions(
        self, campaign: Campaign, result: GateResult, adsets: list[AdSet]
    ) -> tuple[list[Action], list[Action]]:
        if result.decision is Decision.BUDGET_UP:
            pct = float(result.evidence.get("step_pct", 0.0))
            current = campaign.daily_budget
            if current is None or current <= 0:
                return (
                    [],
                    [
                        Action(
                            verb="campaign.budget_increase",
                            target_id=campaign.id,
                            params={"pct": pct, "blocked": "current budget unreported"},
                            authorized=False,
                            rationale=result.reasons[0],
                            source_gate=Decision.BUDGET_UP,
                        )
                    ],
                )
            new_amount = current * (1.0 + min(pct, BUDGET_INCREASE_HARD_CAP_PCT) / 100.0)
            return (
                [
                    Action(
                        verb="campaign.budget_increase",
                        target_id=campaign.id,
                        params={
                            "pct": min(pct, BUDGET_INCREASE_HARD_CAP_PCT),
                            "current_daily_budget": current,
                            "new_daily_budget": new_amount,
                            "stage": Stage.SCALE.value,
                        },
                        authorized=self.config.envelope.is_authorized("campaign.budget_increase"),
                        rationale=result.reasons[0],
                        source_gate=Decision.BUDGET_UP,
                    )
                ],
                [],
            )
        if result.decision is Decision.BUDGET_DOWN:
            pct = float(result.evidence.get("down_pct", 0.0))
            current = campaign.daily_budget
            if current is None or current <= 0:
                return [], []
            return (
                [
                    Action(
                        verb="campaign.budget_decrease",
                        target_id=campaign.id,
                        params={
                            "pct": pct,
                            "current_daily_budget": current,
                            "new_daily_budget": current * (1.0 - pct / 100.0),
                            "stage": Stage.SCALE.value,
                        },
                        authorized=self.config.envelope.is_authorized("campaign.budget_decrease"),
                        rationale=result.reasons[0],
                        source_gate=Decision.BUDGET_DOWN,
                    )
                ],
                [],
            )
        if result.decision is Decision.RECOVER:
            # §8: reactivate paused ad sets in a qualifying Reserve campaign.
            paused = [
                s
                for s in adsets
                if s.campaign_id == campaign.id and not _active(s)
            ]
            actions = [
                Action(
                    verb="reserve.reactivate",
                    target_id=s.id,
                    params={"campaign_id": campaign.id, "entity_type": "adset"},
                    authorized=self.config.envelope.is_authorized("reserve.reactivate"),
                    rationale=result.reasons[0],
                    source_gate=Decision.RECOVER,
                )
                for s in paused
            ]
            return actions, []
        return [], []

    # --- the run -----------------------------------------------------------------

    def run(self, now: Optional[datetime] = None) -> RunResult:
        now = now or datetime.now()
        config = self.config
        snapshot = self.adapter.fetch_entities()

        campaigns = [_enrich(c, config) for c in snapshot.campaigns]
        adsets = [_enrich(s, config) for s in snapshot.adsets]
        ads = [_enrich(a, config) for a in snapshot.ads]

        delivering = [a for a in ads if _active(a)]
        already_dark = [a for a in ads if not _active(a)]

        baselines = compute_baselines(adsets, config)

        markets = sorted({a.market for a in delivering if a.market})
        contexts: dict[str, GateContext] = {
            market: GateContext(
                config=config,
                baselines=baselines,
                market=market,
                now=now,
                market_ads=tuple(a for a in delivering if a.market == market),
            )
            for market in markets
        }

        self.preflights: list[Any] = []
        actions: list[Action] = []
        proposals: list[Action] = []
        watchlist: list[GateResult] = []
        resolutions: list[AdResolution] = []

        for ad in delivering:
            ctx = contexts.get(ad.market or "")
            results = self._ad_results(ad, ctx) if ctx is not None else []
            resolution = self._resolve(ad, results)
            resolutions.append(resolution)
            winner = resolution.winner
            if winner is None:
                continue
            if winner.decision is Decision.WATCH:
                watchlist.append(winner)
                continue
            if winner.decision in RETIREMENTS:
                got, proposed = self._retirement_actions(ad, winner, now, adsets)
            elif winner.decision is Decision.GRADUATE:
                got, proposed = self._graduate_actions(ad, winner, now, adsets)
            elif winner.decision is Decision.KILL:
                got = [
                    self._pause_action(
                        ad, winner, f"KILL ({winner.evidence.get('limb')}): terminal pause (§4)."
                    )
                ]
                proposed = []
            else:  # the §7.1 auction conversion — a proposal, never an action
                got, proposed = self._auction_actions(ad, winner)
            actions.extend(got)
            proposals.extend(proposed)

        campaign_results: list[GateResult] = []
        for campaign in campaigns:
            if campaign.market is None:
                continue
            ctx = contexts.setdefault(
                campaign.market,
                GateContext(
                    config=config,
                    baselines=baselines,
                    market=campaign.market,
                    now=now,
                ),
            )
            for result in self.budget.evaluate(campaign, ctx):
                campaign_results.append(result)
                got, proposed = self._campaign_actions(campaign, result, adsets)
                actions.extend(got)
                proposals.extend(proposed)
        for adset in adsets:
            if adset.market is None:
                continue
            ctx = contexts.setdefault(
                adset.market,
                GateContext(config=config, baselines=baselines, market=adset.market, now=now),
            )
            for result in self.budget.evaluate(adset, ctx):
                # §8: ad-set budgets are frozen — qualifying ad sets are
                # reported on the watchlist, never raised.
                watchlist.append(result)

        # §10 — guards judge the data the actions were computed from.
        paused_ids = {
            a.target_id for a in actions if a.verb in ("ad.pause", "adset.pause")
        }
        paused_spend = sum(
            a.recent.spend or 0.0
            for a in delivering
            if a.id in paused_ids and a.recent is not None
        )
        pipeline_spend = sum(a.recent.spend or 0.0 for a in delivering if a.recent)
        guard = evaluate_guards(
            actions,
            RunSnapshot(
                pull_complete=snapshot.pull_complete,
                pull_errors=tuple(snapshot.errors),
                account_recent_return=_account_return(snapshot),
                pipeline_recent_spend=pipeline_spend,
                paused_recent_spend=paused_spend,
            ),
            config,
        )

        return RunResult(
            config=config,
            snapshot=snapshot,
            baselines=baselines,
            resolutions=resolutions,
            actions=actions,
            proposals=proposals,
            watchlist=watchlist,
            already_dark=already_dark,
            campaign_results=campaign_results,
            guard=guard,
            daily_spend=_daily_spend(campaigns, config),
            preflights=list(self.preflights),
        )


def run_pipeline(adapter: AdPlatformAdapter, config: Config) -> RunResult:
    """Convenience entry point used by the CLI."""
    return Pipeline(adapter, config).run()
