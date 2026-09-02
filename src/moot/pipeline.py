"""The run pipeline: filter → baseline → gates → precedence → actions → guards.

Implements the §12 precedence table exactly: exactly one transition per ad
per run, the losing gates recorded on the winner's ``suppressed_by`` so a
suppressed decision stays visible. Entities that are not genuinely delivering
are reported as already-dark rather than judged (§4 preamble).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Optional

from moot.adapters.base import AdPlatformAdapter, EntitySnapshot
from moot.baselines import MarketBaseline, compute_baselines
from moot.config import Config
from moot.council import hard_vetoes, post_concentration
from moot.duplication import PreflightResult, preflight
from moot.gates.base import GateContext, auction_check
from moot.gates.budget import BudgetGate
from moot.gates.demote import DemoteGate
from moot.gates.fatigue import FatigueGate, WatchGate
from moot.gates.graduate import GraduateGate
from moot.gates.kill import KillGate
from moot.guards import GuardVerdict, RunSnapshot, evaluate_guards
from moot.models import (
    Action,
    Ad,
    AdSet,
    Campaign,
    Decision,
    GateResult,
    Stage,
)
from moot.writes import BUDGET_INCREASE_HARD_CAP_PCT

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

#: Actions whose target id is an ad — the only ones that can carry a post's
#: concentration figure (docs/debate-protocol.md §1).
AD_LEVEL_VERBS = frozenset(
    {"ad.pause", "ad.activate", "duplicate.post_id", "budget.bid_review"}
)


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
    preflights: list[PreflightResult] = field(default_factory=list)
    # The enriched entities (market/stage derived through the stage map) — the
    # raw snapshot has stage unset on live adapters, so the scorecard and the
    # debate-context builder read these.
    ads: list[Ad] = field(default_factory=list)
    campaigns: list[Campaign] = field(default_factory=list)
    adsets: list[AdSet] = field(default_factory=list)


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
    """True only when the entity is genuinely delivering (§4 preamble)."""
    return (getattr(entity, "effective_status", None) or "").upper() == "ACTIVE"


def _destination_page_id(config: Config, market: Optional[str]) -> Optional[str]:
    """The market's configured destination page — the duplication's anchor.

    Without it the creative cannot reference the post, so a duplication would
    mint a fresh one (§9 B spirit); every duplication site checks this first.
    """
    market_cfg = config.markets.get(market or "") if market else None
    return market_cfg.destination_page_id if market_cfg is not None else None


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


def _with_concentration(
    action: Action, ads_by_id: dict[str, Ad], shares: dict[tuple[str, str, str], float]
) -> Action:
    """Stamp the target ad's post concentration into the action evidence.

    ``council.contested()`` reads ``revenue_share`` to decide whether an
    action on a concentrated post is worth arguing about (§1), and the debate
    brief states the same figure — one computation, both consumers.
    """
    if action.verb not in AD_LEVEL_VERBS:
        return action
    ad = ads_by_id.get(action.target_id)
    if ad is None or not ad.post_id or ad.market is None or ad.stage is None:
        return action
    share = shares.get((ad.market, ad.stage.value, ad.post_id))
    if share is None:
        return action
    return replace(action, evidence={**action.evidence, "revenue_share": share})


class Pipeline:
    """One autonomous run, computed end to end but never dispatched here."""

    def __init__(self, adapter: AdPlatformAdapter, config: Config):
        self.adapter = adapter
        self.config = config
        self.preflights: list[PreflightResult] = []
        # §9 A within-run idempotency: pre-flight only sees pre-run state, so
        # the pipeline tracks what THIS run has already scheduled — two ads
        # sharing a post (the normal case, gates.md §4 D) must not produce
        # duplicate copies or duplicate cohort ad sets in one run.
        self._seen_duplicate_keys: set[tuple[str, str]] = set()  # (campaign, post)
        self._seen_cohort_adsets: set[tuple[str, str]] = set()  # (campaign, name)
        self.kill = KillGate()
        self.graduate = GraduateGate()
        self.fatigue = FatigueGate()
        self.watch = WatchGate()
        self.demote = DemoteGate()
        self.budget = BudgetGate()

    # --- gate execution ---------------------------------------------------------

    def _ad_results(self, ad: Ad, ctx: GateContext) -> list[GateResult]:
        """Every gate's verdict on one ad, with the §7.1 conversion applied.

        The auction check is §12 rank 3 — it converts a retirement into a
        budget proposal BEFORE precedence runs, because retiring a
        repriced-but-healthy asset is the costliest mistake available.
        """
        results: list[GateResult] = []
        for gate in (self.kill, self.graduate, self.fatigue, self.demote, self.watch):
            results.extend(gate.evaluate(ad, ctx))

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

    # --- action construction ------------------------------------------------------

    def _pause_action(
        self,
        ad: Ad,
        result: GateResult,
        rationale: str,
        requires_verified_duplicate_of: Optional[str] = None,
    ) -> Action:
        """An ad pause, optionally held until a duplication verifies (§6/§7)."""
        params: dict[str, Any] = {
            "entity_type": "ad",
            "status": "PAUSED",
            "ad_name": ad.name,
        }
        if requires_verified_duplicate_of is not None:
            # §6/§7: the pause follows a copy that verifies — the write layer
            # holds it until the duplication verifies ACTIVE.
            params["requires_verified_duplicate_of"] = requires_verified_duplicate_of
        return Action(
            verb="ad.pause",
            target_id=ad.id,
            params=params,
            authorized=self.config.envelope.is_authorized("ad.pause"),
            rationale=rationale,
            source_gate=result.decision,
            evidence=dict(result.evidence),
        )

    def _blocked_duplicate(
        self,
        ad: Ad,
        decision: Decision,
        *,
        blocked: str,
        rationale: str,
        evidence: Optional[dict[str, Any]] = None,
    ) -> Action:
        """A duplication this run refuses to perform, reported as a proposal.

        Whether blocked by routing (§9 B), a missing destination page, or
        within-run idempotency (§9 A), the shape is the same: an unauthorized
        proposal carrying the post id and the reason, so the operator sees
        what was withheld and why.
        """
        return Action(
            verb="duplicate.post_id",
            target_id=ad.id,
            params={"blocked": blocked, "post_id": ad.post_id},
            authorized=False,
            rationale=rationale,
            source_gate=decision,
            evidence=dict(evidence or {}),
        )

    def _cohort_action(
        self, checks: PreflightResult, source_decision: Decision,
        source_evidence: Optional[dict[str, Any]] = None,
    ) -> Optional[Action]:
        """The §5 cohort-ad-set creation, once per (campaign, name) per run."""
        name = checks.destination_adset_to_create or ""
        campaign_id = checks.destination_campaign_id or ""
        key = (campaign_id, name)
        if key in self._seen_cohort_adsets:
            return None  # this run already scheduled its creation
        self._seen_cohort_adsets.add(key)
        return Action(
            verb="adset.create_cohort",
            target_id=campaign_id,
            params={
                "name": name,
                "campaign_id": campaign_id,
                "pixel_id": self.config.pixel.get("id", ""),
                "born": "PAUSED",
            },
            authorized=self.config.envelope.is_authorized("adset.create_cohort"),
            rationale="destination ad set absent — created PAUSED (§5 action)",
            source_gate=source_decision,
            evidence=dict(source_evidence or {}),
        )

    def _duplicate_action(
        self,
        ad: Ad,
        checks: PreflightResult,
        source_decision: Decision,
        destination_stage: Stage,
        *,
        rationale: str,
        source_evidence: Optional[dict[str, Any]] = None,
    ) -> Optional[Action]:
        """One duplicate per (destination campaign, post id) per run (§9 A).

        Returns None when this run already scheduled a copy of the post — the
        caller reports an already-present proposal instead. The source ad's
        destination_url travels in the params so the council's brand veto can
        judge it mechanically (docs/agents.md, council.hard_vetoes). Every
        copy is born PAUSED and activated only after it verifies (§5) — the
        Reserve copy too: §6/§7's source pause follows a copy that verifies
        ACTIVE, so the copy must actually reach ACTIVE.
        """
        campaign_id = checks.destination_campaign_id or ""
        key = (campaign_id, ad.post_id or "")
        if key in self._seen_duplicate_keys:
            return None
        self._seen_duplicate_keys.add(key)
        params: dict[str, Any] = {
            "destination_campaign_id": campaign_id,
            "destination_adset_id": checks.destination_adset_id,
            "destination_adset_name": checks.destination_adset_to_create,
            "destination_stage": destination_stage.value,
            "post_id": ad.post_id,
            "url_tags": ad.url_tags,
            "page_id": _destination_page_id(self.config, ad.market),
            "destination_url": ad.destination_url,
            "activate_after_verify": True,
        }
        return Action(
            verb="duplicate.post_id",
            target_id=ad.id,
            params=params,
            authorized=self.config.envelope.is_authorized("duplicate.post_id"),
            rationale=rationale,
            source_gate=source_decision,
            evidence=dict(source_evidence or {}),
        )

    def _duplication_actions(
        self,
        ad: Ad,
        checks: PreflightResult,
        decision: Decision,
        destination_stage: Stage,
        *,
        rationale: str,
        source_evidence: Optional[dict[str, Any]] = None,
    ) -> tuple[list[Action], Optional[Action]]:
        """The pre-flight-ok duplication path shared by §5, §6 and §7.

        Creates the destination cohort ad set when absent, then the duplicate
        itself — carrying the winning gate's evidence, because the debate
        brief must show "the gate that fired with its evidence dict"
        (docs/debate-protocol.md §2 Round 0). Returns
        ``(actions, already_present)`` — exactly one side non-empty, because
        §9 A within-run idempotency means this run either schedules the copy
        or reports that it already did.
        """
        actions: list[Action] = []
        if checks.destination_adset_to_create:
            cohort = self._cohort_action(checks, decision)
            if cohort is not None:
                actions.append(cohort)
        duplicate = self._duplicate_action(
            ad, checks, decision, destination_stage,
            rationale=rationale, source_evidence=source_evidence,
        )
        if duplicate is None:
            # §9 A within-run: this run already scheduled a copy of the post
            # into this campaign — report, do not duplicate again.
            return [], self._blocked_duplicate(
                ad, decision,
                blocked="already-present",
                rationale=(
                    f"post {ad.post_id} already scheduled into campaign "
                    f"{checks.destination_campaign_id} earlier this run "
                    "(§9 A within-run idempotency)."
                ),
            )
        actions.append(duplicate)
        return actions, None

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
        if checks.status != "ok":
            # blocked-routing: propose only; do not pause the source.
            return (
                [],
                [self._blocked_duplicate(
                    ad, result.decision,
                    blocked=checks.status,
                    rationale="; ".join(checks.reasons),
                    evidence=result.evidence,
                )],
            )
        if not _destination_page_id(self.config, ad.market):
            # No page → the creative cannot reference the post; propose,
            # never mint a fresh one (§9 B spirit).
            return (
                [],
                [self._blocked_duplicate(
                    ad, result.decision,
                    blocked="no-destination-page",
                    rationale=(
                        f"market {ad.market!r} has no destination_page_id — "
                        "duplicating without it would mint a fresh post; "
                        "retirement held (source keeps delivering)."
                    ),
                )],
            )
        actions, already_present = self._duplication_actions(
            ad, checks, result.decision, Stage.RESERVE,
            rationale=result.reasons[0] if result.reasons else "retirement",
            source_evidence=result.evidence,
        )
        if already_present is not None:
            return [], [already_present]
        # §6: pause the source AFTER the copy verifies ACTIVE. The write
        # layer enforces the ordering via requires_verified_duplicate_of.
        actions.append(
            self._pause_action(
                ad, result,
                f"{result.decision.value}: pause source after the Reserve "
                "copy verifies (§6/§7 action).",
                requires_verified_duplicate_of=ad.id,
            )
        )
        return actions, []

    def _graduate_actions(
        self, ad: Ad, result: GateResult, now: datetime, adsets: list[AdSet]
    ) -> tuple[list[Action], list[Action]]:
        """§5 action: §9 pre-flight, duplicate into the destination market's
        current month cohort ad set, born PAUSED. Do NOT pause the source —
        the original keeps harvesting its own audience (framework.md §2.1)."""
        checks = preflight(self.adapter, ad, Stage.SCALE, adsets, self.config, now)
        self.preflights.append(checks)
        proposed_only = bool(result.evidence.get("proposed_only"))
        if checks.status == "already-present":
            return [], []
        if checks.status != "ok":
            return (
                [],
                [self._blocked_duplicate(
                    ad, Decision.GRADUATE,
                    blocked=checks.status,
                    rationale="; ".join(checks.reasons),
                    evidence=result.evidence,
                )],
            )
        if not _destination_page_id(self.config, ad.market):
            return (
                [],
                [self._blocked_duplicate(
                    ad, Decision.GRADUATE,
                    blocked="no-destination-page",
                    rationale=(
                        f"market {ad.market!r} has no destination_page_id — "
                        "duplicating without it would mint a fresh post."
                    ),
                )],
            )
        actions, already_present = self._duplication_actions(
            ad, checks, Decision.GRADUATE, Stage.SCALE,
            rationale=result.reasons[0] if result.reasons else "graduation",
            source_evidence=result.evidence,
        )
        if already_present is not None:
            return [], [already_present]
        if proposed_only:
            # §5 Path B note: past the 1.80 × baseline ceiling a graduation
            # is proposed, not executed.
            actions = [a.as_proposal("proposed only (§5 Path B ceiling)") for a in actions]
        return actions, []

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
                    evidence=dict(result.evidence),
                )
            ],
        )

    def _winner_actions(
        self, ad: Ad, winner: GateResult, now: datetime, adsets: list[AdSet]
    ) -> tuple[list[Action], list[Action]]:
        """Turn one ad's winning gate result into its actions and proposals."""
        if winner.decision in RETIREMENTS:
            return self._retirement_actions(ad, winner, now, adsets)
        if winner.decision is Decision.GRADUATE:
            return self._graduate_actions(ad, winner, now, adsets)
        if winner.decision is Decision.KILL:
            return (
                [self._pause_action(
                    ad, winner,
                    f"KILL ({winner.evidence.get('limb')}): terminal pause (§4)."
                )],
                [],
            )
        # the §7.1 auction conversion — a proposal, never an action
        return self._auction_actions(ad, winner)

    def _campaign_actions(
        self, campaign: Campaign, result: GateResult, adsets: list[AdSet]
    ) -> tuple[list[Action], list[Action]]:
        """§8 campaign-level writes: scale up, scale down, Reserve recovery."""
        if result.decision is Decision.BUDGET_UP:
            return self._budget_up_action(campaign, result)
        if result.decision is Decision.BUDGET_DOWN:
            return self._budget_down_action(campaign, result)
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

    def _budget_up_action(
        self, campaign: Campaign, result: GateResult
    ) -> tuple[list[Action], list[Action]]:
        """§8 scale-up: the step is capped again here so the report never
        shows an ask the write layer would refuse."""
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
                        evidence=dict(result.evidence),
                    )
                ],
            )
        capped = min(pct, BUDGET_INCREASE_HARD_CAP_PCT)
        return (
            [
                Action(
                    verb="campaign.budget_increase",
                    target_id=campaign.id,
                    params={
                        "pct": capped,
                        "current_daily_budget": current,
                        "new_daily_budget": current * (1.0 + capped / 100.0),
                        "stage": Stage.SCALE.value,
                    },
                    authorized=self.config.envelope.is_authorized(
                        "campaign.budget_increase"
                    ),
                    rationale=result.reasons[0],
                    source_gate=Decision.BUDGET_UP,
                    evidence=dict(result.evidence),
                )
            ],
            [],
        )

    def _budget_down_action(
        self, campaign: Campaign, result: GateResult
    ) -> tuple[list[Action], list[Action]]:
        """§8 scale-down: cutting a loser is not scaling — always authorised."""
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
                    authorized=self.config.envelope.is_authorized(
                        "campaign.budget_decrease"
                    ),
                    rationale=result.reasons[0],
                    source_gate=Decision.BUDGET_DOWN,
                    evidence=dict(result.evidence),
                )
            ],
            [],
        )

    # --- the run -----------------------------------------------------------------

    def _reset_run_state(self) -> None:
        """Clear the within-run idempotency ledgers so a reused Pipeline
        object cannot leak one run's schedule into the next."""
        self.preflights = []
        self._seen_duplicate_keys = set()
        self._seen_cohort_adsets = set()

    def _market_contexts(
        self, delivering: list[Ad], baselines: dict[str, MarketBaseline], now: datetime
    ) -> dict[str, GateContext]:
        """One GateContext per delivering market — baselines are per-market
        (§3) and kill limb D aggregates across the whole market (§4 D)."""
        markets = sorted({a.market for a in delivering if a.market})
        return {
            market: GateContext(
                config=self.config,
                baselines=baselines,
                market=market,
                now=now,
                market_ads=tuple(a for a in delivering if a.market == market),
            )
            for market in markets
        }

    def _context_for(
        self,
        contexts: dict[str, GateContext],
        market: str,
        baselines: dict[str, MarketBaseline],
        now: datetime,
    ) -> GateContext:
        """The market's context, created on demand for the §8 passes.

        Campaigns and ad sets iterate markets the ad pass may not have touched
        (a market with no delivering ads still has budgets to judge).
        """
        return contexts.setdefault(
            market,
            GateContext(
                config=self.config, baselines=baselines, market=market, now=now
            ),
        )

    def _ad_pass(
        self,
        delivering: list[Ad],
        contexts: dict[str, GateContext],
        adsets: list[AdSet],
        now: datetime,
    ) -> tuple[list[AdResolution], list[Action], list[Action], list[GateResult]]:
        """Judge every delivering ad: gates → §12 precedence → actions."""
        resolutions: list[AdResolution] = []
        actions: list[Action] = []
        proposals: list[Action] = []
        watchlist: list[GateResult] = []
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
            got, proposed = self._winner_actions(ad, winner, now, adsets)
            actions.extend(got)
            proposals.extend(proposed)
        return resolutions, actions, proposals, watchlist

    def _budget_pass(
        self,
        campaigns: list[Campaign],
        adsets: list[AdSet],
        contexts: dict[str, GateContext],
        baselines: dict[str, MarketBaseline],
        now: datetime,
    ) -> tuple[list[GateResult], list[Action], list[Action], list[GateResult]]:
        """§8 over campaigns and ad sets.

        Campaigns produce budget actions; ad sets can only be REPORTED —
        ad-set budgets are frozen to autonomous increase in every stage, so a
        qualifying ad set lands on the watchlist, never on the action list.
        """
        campaign_results: list[GateResult] = []
        actions: list[Action] = []
        proposals: list[Action] = []
        watchlist: list[GateResult] = []
        for campaign in campaigns:
            if campaign.market is None:
                continue
            ctx = self._context_for(contexts, campaign.market, baselines, now)
            for result in self.budget.evaluate(campaign, ctx):
                campaign_results.append(result)
                got, proposed = self._campaign_actions(campaign, result, adsets)
                actions.extend(got)
                proposals.extend(proposed)
        for adset in adsets:
            if adset.market is None:
                continue
            ctx = self._context_for(contexts, adset.market, baselines, now)
            watchlist.extend(self.budget.evaluate(adset, ctx))
        return campaign_results, actions, proposals, watchlist

    def _run_snapshot(
        self,
        snapshot: EntitySnapshot,
        actions: list[Action],
        delivering: list[Ad],
    ) -> RunSnapshot:
        """The §10 facts the guards judge.

        Ads whose recent spend is UNREPORTED are excluded from both sides of
        the anomaly share (an ``or 0`` denominator would make a mass pause
        read artificially small exactly when the data is partial) and are
        surfaced as a spend_unknown figure the guards can trip on.
        """
        paused_ids = {
            a.target_id for a in actions if a.verb in ("ad.pause", "adset.pause")
        }
        known_spend: list[tuple[str, float]] = []  # (ad id, recent spend)
        unknown_count = 0
        for ad in delivering:
            if ad.recent is None or ad.recent.spend is None:
                unknown_count += 1
                continue
            known_spend.append((ad.id, ad.recent.spend))
        pipeline_spend = sum(spend for _, spend in known_spend)
        paused_spend = sum(spend for ad_id, spend in known_spend if ad_id in paused_ids)
        spend_unknown = 0.0
        if unknown_count and known_spend:
            # Estimate only, and only for the guard: unknown-spend ads count
            # at the mean of their known neighbours. A guard may act on the
            # suspicion of missing data; no gate ever reads this figure.
            spend_unknown = (pipeline_spend / len(known_spend)) * unknown_count
        return RunSnapshot(
            pull_complete=snapshot.pull_complete,
            pull_errors=tuple(snapshot.errors),
            account_recent_return=_account_return(snapshot),
            pipeline_recent_spend=pipeline_spend,
            paused_recent_spend=paused_spend,
            spend_unknown=spend_unknown,
            spend_unknown_ads=unknown_count,
        )

    def _apply_vetoes(
        self, actions: list[Action], vetoes: list[dict[str, Any]]
    ) -> tuple[list[Action], list[Action]]:
        """Downgrade brand-vetoed duplicates to proposals.

        The brand guardian's destination veto is mechanical (council.py,
        docs/agents.md): a duplicate whose landing page violates
        policy.destination is downgraded to a proposal carrying the veto
        reason — never executed, never silently dropped.
        """
        kept: list[Action] = []
        downgraded: list[Action] = []
        for action in actions:
            if "destination_url" not in action.params:
                kept.append(action)
                continue
            veto = next((v for v in vetoes if v["scope"] == action.target_id), None)
            if veto is None:
                kept.append(action)
                continue
            downgraded.append(
                action.as_proposal(
                    f"HARD VETO ({veto['by']}): {veto['reason']} — "
                    "downgraded to a proposal"
                )
            )
        return kept, downgraded

    def run(self, now: Optional[datetime] = None) -> RunResult:
        """Compute one full run. ``now`` defaults to the current UTC moment —
        timezone-aware, so a cohort ad set's name cannot differ across a month
        boundary depending on which module computed it."""
        now = now or datetime.now(timezone.utc)
        config = self.config
        snapshot = self.adapter.fetch_entities()

        campaigns = [_enrich(c, config) for c in snapshot.campaigns]
        adsets = [_enrich(s, config) for s in snapshot.adsets]
        ads = [_enrich(a, config) for a in snapshot.ads]

        delivering = [a for a in ads if _active(a)]
        already_dark = [a for a in ads if not _active(a)]

        baselines = compute_baselines(adsets, config)
        contexts = self._market_contexts(delivering, baselines, now)

        self._reset_run_state()
        resolutions, actions, proposals, watchlist = self._ad_pass(
            delivering, contexts, adsets, now
        )
        campaign_results, budget_actions, budget_proposals, budget_watch = (
            self._budget_pass(campaigns, adsets, contexts, baselines, now)
        )
        actions.extend(budget_actions)
        proposals.extend(budget_proposals)
        watchlist.extend(budget_watch)

        # §1 concentration: stamp each ad-level action with its post's share
        # of stage revenue, for contested() and the debate brief alike.
        shares = post_concentration(delivering)
        ads_by_id = {a.id: a for a in ads}
        actions = [_with_concentration(a, ads_by_id, shares) for a in actions]
        proposals = [_with_concentration(a, ads_by_id, shares) for a in proposals]

        guard = evaluate_guards(
            actions,
            self._run_snapshot(snapshot, actions, delivering),
            config,
        )
        actions, vetoed = self._apply_vetoes(
            actions, hard_vetoes(actions, guard_flagged=not guard.writes_allowed,
                                 config=config)
        )
        proposals.extend(vetoed)

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
            ads=ads,
            campaigns=campaigns,
            adsets=adsets,
        )
