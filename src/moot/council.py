"""The adversarial council — docs/framework.md §7, docs/debate-protocol.md.

The gates decide what is *eligible*; they cannot decide what is *wise*,
because every gate encodes a worldview and every worldview has a blind spot.
This module defines the roster of opposed mandates and marks the actions they
would contest. It does NOT call an LLM: stances here are mechanical keyword
positions, and the two HARD vetoes are enforced in code, not prose.

The Round 0 renderers — :func:`brief` and :func:`charter_block` — are what
external LLM agents receive (docs/debate-protocol.md §2); the later rounds
live in the plugin layer, which owns the prompts. Round 0 is shared and
role-neutral: every councillor gets the same numbers, because a brief without
numbers makes the protocol's own calibration criterion ("numbers appear in
every argument") unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from moot.baselines import MarketBaseline
from moot.config import Config, DestinationPolicy
from moot.models import (
    Action,
    Ad,
    AdSet,
    Campaign,
    CreativeType,
    Metrics,
    Stage,
)

SUPPORT = 1
OPPOSE = -1

#: Rendered for any value the run could not report. A synthesised zero is a
#: fabricated fact (docs/gates.md §11.2); in a brief it becomes an argument.
UNAVAILABLE = "—"


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


def _destination_veto(
    action: Action, policy: DestinationPolicy
) -> Optional[dict[str, Any]]:
    """The brand archetype's mechanical destination veto for one action.

    Forbidden/required patterns, plus the ``require_tracking_params`` check:
    a duplication that carries no ``utm_*`` url_tags is invisible to every
    downstream analytics surface (§9 C) and is vetoed when the policy demands
    tracking parameters.
    """
    destination = action.params.get("destination_url")
    if not isinstance(destination, str):
        return None
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
    if not (violated or missing):
        return None
    return {"by": "brand", "scope": action.target_id, "reason": reason}


def hard_vetoes(
    actions: list[Action], guard_flagged: bool, config: Config
) -> list[dict[str, Any]]:
    """The two vetoes enforced mechanically, not by prose.

    1. The risk archetype vetoes any action set the anomaly guard flags.
    2. The brand archetype vetoes any action whose destination URL violates
       ``config.policy.destination``.

    An agent that can be argued out of a safety rule does not have a safety
    rule (docs/agents.md), so neither veto is debatable.
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
    for action in actions:
        veto = _destination_veto(action, config.destination_policy)
        if veto is not None:
            vetoes.append(veto)
    return vetoes


# --- concentration (§1) ---------------------------------------------------------


def post_concentration(ads: Sequence[Ad]) -> dict[tuple[str, str, str], float]:
    """Each post's share of its (market, stage) recent revenue.

    Grouped by market AND stage — a stage's campaigns are per-market, so a
    post's revenue is measured against the auction it actually runs in. Only
    genuinely delivering ads contribute (§4 preamble: a paused ad's in-flight
    figures are not the stage's live revenue). Unreported revenue (§11.2)
    stays out of both numerator and denominator, and a stage with no recorded
    revenue yields no shares — never a synthesised 0.
    """
    stage_revenue: dict[tuple[str, str], float] = {}
    post_revenue: dict[tuple[str, str, str], float] = {}
    for ad in ads:
        if (ad.effective_status or "").upper() != "ACTIVE":
            continue
        market = ad.market or ""
        stage = ad.stage.value if ad.stage else ""
        value = None if ad.recent is None else ad.recent.purchase_value
        if not market or not stage or value is None:
            continue
        stage_key = (market, stage)
        stage_revenue[stage_key] = stage_revenue.get(stage_key, 0.0) + value
        if ad.post_id:
            post_key = (market, stage, ad.post_id)
            post_revenue[post_key] = post_revenue.get(post_key, 0.0) + value
    return {
        post_key: value / stage_revenue[(post_key[0], post_key[1])]
        for post_key, value in post_revenue.items()
        if stage_revenue[(post_key[0], post_key[1])] > 0
    }


# --- the Round 0 brief and its context (docs/debate-protocol.md §2) -------------


@dataclass(frozen=True)
class DebateContext:
    """Every number the Round 0 brief promises (docs/debate-protocol.md §2).

    The entity, its metrics across both windows, the market baseline with its
    ``baseline_source``, the stage's spend and return, the account target and
    the concentration figure. Absence is preserved as ``None`` everywhere —
    the brief renders "—", never 0, because a synthesised zero in a brief
    becomes an argument (§11.2).
    """

    target: float
    baseline: Optional[float] = None
    baseline_source: str = ""  # "computed" | "fallback" | "seeded" | "" (none)
    baseline_population: int = 0
    stage: Optional[Stage] = None
    stage_spend: Optional[float] = None
    stage_return: Optional[float] = None
    #: This post's share of its (market, stage) recent revenue, 0..1 — None
    #: when the stage recorded no revenue to share.
    concentration: Optional[float] = None
    recent_window_days: int = 7
    trailing_window_days: int = 30
    ad_name: Optional[str] = None
    market: Optional[str] = None
    # Rendering support for "its metrics across both windows": the same
    # Metrics objects the gates judged, absence preserved.
    entity_id: str = ""
    entity_kind: str = ""  # "ad" | "adset" | "campaign" | "" (unresolved)
    recent: Optional[Metrics] = None
    trailing: Optional[Metrics] = None
    creative_type: Optional[CreativeType] = None


def _resolve_entity(
    target_id: str,
    campaigns: Sequence[Campaign],
    adsets: Sequence[AdSet],
    ads: Sequence[Ad],
) -> Optional[Ad | AdSet | Campaign]:
    """The entity an action targets, looked up by id.

    Ads first — most contested actions are ad-level — then campaigns, then ad
    sets (``reserve.reactivate`` targets one). Returns None for an id the run
    never saw; the brief still renders, with "—" where the numbers would be.
    """
    for ad in ads:
        if ad.id == target_id:
            return ad
    for campaign in campaigns:
        if campaign.id == target_id:
            return campaign
    for adset in adsets:
        if adset.id == target_id:
            return adset
    return None


def _stage_figures(
    campaigns: Sequence[Campaign], market: str, stage: Stage
) -> tuple[Optional[float], Optional[float]]:
    """The (market, stage) cell's recent spend and return, from campaigns.

    A stage's campaigns are its container per market, and this is the same
    basis the §8 spend duty and the run report use — so "the stage's spend
    and return" is one number everywhere it appears. Unreported figures keep
    the aggregate None rather than 0 (§11.2).
    """
    spend = 0.0
    value = 0.0
    seen_spend = False
    seen_value = False
    for campaign in campaigns:
        if campaign.market != market or campaign.stage is not stage:
            continue
        if campaign.recent is None:
            continue
        if campaign.recent.spend is not None:
            spend += campaign.recent.spend
            seen_spend = True
        if campaign.recent.purchase_value is not None:
            value += campaign.recent.purchase_value
            seen_value = True
    stage_spend = spend if seen_spend else None
    stage_return = (value / spend) if (seen_value and spend > 0) else None
    return stage_spend, stage_return


def _entity_placement(
    entity: Optional[Ad | AdSet | Campaign], config: Config
) -> tuple[Optional[str], Optional[Stage]]:
    """The entity's market and stage, derived through the stage map when the
    platform did not report them (framework.md §4: routing facts are derived,
    never name-inferred)."""
    market = getattr(entity, "market", None)
    stage = getattr(entity, "stage", None)
    if (market is None or stage is None) and isinstance(entity, (Ad, AdSet)):
        entry = config.locate_campaign(entity.campaign_id or "")
        if entry is not None:
            market = market or entry.market
            stage = stage or entry.stage
    return market, stage


def build_debate_context(
    action: Action,
    *,
    baselines: dict[str, MarketBaseline],
    campaigns: Sequence[Campaign],
    adsets: Sequence[AdSet],
    ads: Sequence[Ad],
    config: Config,
) -> DebateContext:
    """Assemble one action's :class:`DebateContext` from the run state.

    The pipeline has already computed every figure the brief promises — this
    resolves which entity the action targets and gathers its numbers. Pass the
    ENRICHED entity lists (``RunResult.ads`` / ``.campaigns`` / ``.adsets``):
    the raw snapshot leaves ``stage`` unset on the live adapter, and a brief
    that cannot state the stage cannot state concentration either.
    """
    entity = _resolve_entity(action.target_id, campaigns, adsets, ads)
    market, stage = _entity_placement(entity, config)

    baseline = baselines.get(market) if market else None
    stage_spend: Optional[float] = None
    stage_return: Optional[float] = None
    concentration: Optional[float] = None
    if market is not None and stage is not None:
        stage_spend, stage_return = _stage_figures(campaigns, market, stage)
        post_id = entity.post_id if isinstance(entity, Ad) else None
        if post_id:
            concentration = post_concentration(ads).get((market, stage.value, post_id))

    return DebateContext(
        target=config.target,
        baseline=baseline.value if baseline is not None else None,
        baseline_source=baseline.source if baseline is not None else "",
        baseline_population=baseline.population if baseline is not None else 0,
        stage=stage,
        stage_spend=stage_spend,
        stage_return=stage_return,
        concentration=concentration,
        recent_window_days=config.windows.recent_days,
        trailing_window_days=config.windows.trailing_days,
        ad_name=entity.name if entity is not None else None,
        market=market,
        entity_id=entity.id if entity is not None else "",
        entity_kind=(
            {Ad: "ad", AdSet: "ad set", Campaign: "campaign"}[type(entity)]
            if entity is not None
            else ""
        ),
        recent=getattr(entity, "recent", None),
        trailing=getattr(entity, "trailing", None),
        creative_type=entity.creative_type if isinstance(entity, Ad) else None,
    )


# --- brief rendering ------------------------------------------------------------


def _money(value: Optional[float]) -> str:
    """Money at 2dp, or "—" — never 0 for an unreported figure (§11.2)."""
    return UNAVAILABLE if value is None else f"{value:,.2f}"


def _count(value: Optional[int]) -> str:
    """A count, or "—": a missing action entry is absence, not zero (§11.2)."""
    return UNAVAILABLE if value is None else f"{value:d}"


def _multiple(value: Optional[float]) -> str:
    """A return figure at 2dp with its × unit (gates.md §1 ``return``)."""
    return UNAVAILABLE if value is None else f"{value:.2f}×"


def _percent(value: Optional[float]) -> str:
    """A decimal-fraction rate as a percentage (§11.4: never rescale the data)."""
    return UNAVAILABLE if value is None else f"{value:.2%}"


def _plain(value: Optional[float]) -> str:
    """A bare figure at 2dp (e.g. frequency), or "—"."""
    return UNAVAILABLE if value is None else f"{value:.2f}"


def _hook_cell(metrics: Optional[Metrics], creative: Optional[CreativeType]) -> str:
    """The hook-rate cell: "n/a (static)" for non-video creative (§11.5).

    Hook rate is undefined for static creative — skipped, never defaulted to
    zero — so the cell says why it is empty rather than showing a fabricated
    number.
    """
    if metrics is not None and metrics.hook_rate is not None:
        return _percent(metrics.hook_rate)
    if creative is not None and creative is not CreativeType.VIDEO:
        return "n/a (static)"
    return UNAVAILABLE


def _metrics_table(context: DebateContext) -> list[str]:
    """The two-column recent-vs-trailing table the Round 0 brief requires.

    Every gate states which window it judged; handing the council only one
    window hides exactly the recent-vs-trailing decline that §6/§7 argue about.
    """
    recent, trailing = context.recent, context.trailing

    def row(
        label: str, pick: Callable[[Metrics], Optional[Any]], fmt: Callable[[Optional[Any]], str]
    ) -> tuple[str, str, str]:
        return (
            label,
            fmt(None if recent is None else pick(recent)),
            fmt(None if trailing is None else pick(trailing)),
        )

    rows = [
        row("Spend", lambda m: m.spend, _money),
        row("Return", lambda m: m.return_, _multiple),
        row("Cost per cart", lambda m: m.cost_per_cart, _money),
        row("Purchases", lambda m: m.purchases, _count),
        row("Carts", lambda m: m.carts, _count),
        row("Outbound CTR", lambda m: m.outbound_ctr, _percent),
        ("Hook rate", _hook_cell(recent, context.creative_type),
         _hook_cell(trailing, context.creative_type)),
        row("CPM", lambda m: m.cpm, _money),
        row("Frequency", lambda m: m.frequency, _plain),
    ]
    lines = [
        f"## Metrics — recent ({context.recent_window_days}d) vs "
        f"trailing ({context.trailing_window_days}d)",
        "",
        "| Metric | Recent | Trailing |",
        "|---|---:|---:|",
    ]
    lines += [f"| {label} | {recent_cell} | {trailing_cell} |"
              for label, recent_cell, trailing_cell in rows]
    lines += [
        "",
        "Money and cost-per-cart in account currency; CTR and hook rate are "
        "percentages (the platform reports decimal fractions, §11.4); "
        "\"—\" means unreported, never zero (§11.2).",
    ]
    return lines


def _action_lines(action: Action) -> list[str]:
    """The action block every round's brief leads with."""
    return [
        "## The action",
        f"- **Verb**: `{action.verb}`",
        f"- **Target**: `{action.target_id}`",
        f"- **Source gate**: "
        f"{action.source_gate.value if action.source_gate else 'none'}",
        f"- **Rationale**: {action.rationale or UNAVAILABLE}",
    ]


def _entity_lines(context: DebateContext) -> list[str]:
    """The entity line-up: name, id, market, stage — the unit under judgement."""
    kind = context.entity_kind or "entity"
    return [
        f"- **{kind.capitalize()}**: {context.ad_name or UNAVAILABLE} "
        f"(`{context.entity_id or UNAVAILABLE}`)",
        f"- **Market**: {context.market or UNAVAILABLE}",
        f"- **Stage**: {context.stage.value if context.stage else UNAVAILABLE}",
    ]


def _evidence_lines(action: Action) -> list[str]:
    """The gate's evidence dict, one readable line per key — not ``str(dict)``.

    This is the evidence the Adjudicator's BASIS field must cite, so it has to
    be legible on its own. Floats round to 2dp for display; the audit JSONL
    keeps the exact values.
    """
    if not action.evidence:
        return [f"- {UNAVAILABLE} — the gate recorded no numeric evidence."]
    lines: list[str] = []
    for key in sorted(action.evidence):
        value = action.evidence[key]
        if isinstance(value, float):
            value = round(value, 2)
        lines.append(f"- {key}: `{value!r}`")
    return lines


def _baseline_lines(context: DebateContext) -> list[str]:
    """The market baseline with value AND source — §3's anti-rot rule.

    The run report states both for the same reason: a fallback baseline
    silently in play is how gates rot.
    """
    if context.market is None:
        return [
            f"- {UNAVAILABLE} — no market resolved for this entity. A gate "
            "without a market must not borrow another market's bar (§3)."
        ]
    source = context.baseline_source or UNAVAILABLE
    warning = (
        " — **a fallback or seeded baseline is in play; this bar was not "
        "earned by this market (§3)**"
        if context.baseline_source in ("fallback", "seeded")
        else ""
    )
    return [
        f"- **Cost-per-cart baseline**: {_money(context.baseline)} "
        f"(candidates: {context.baseline_population})",
        f"- **baseline_source**: `{source}`{warning}",
    ]


def _target_lines(context: DebateContext) -> list[str]:
    """The account target with this entity's recent return stated against it.

    T derives every §2 threshold, so an argument that does not know T cannot
    know what any floor in the evidence was asking.
    """
    ret = None if context.recent is None else context.recent.return_
    if ret is None:
        against = f"{UNAVAILABLE} (recent return unreported)"
    else:
        against = f"**{ret:.2f}×** = {ret / context.target:.2f} × T"
    return [
        f"- **Account target T**: {context.target:.2f}",
        f"- **This entity's recent return against T**: {against}",
    ]


def _concentration_line(context: DebateContext) -> str:
    """The concentration figure, flagged past the §1 threshold.

    When one post carries more than 40% of stage revenue that is fragility,
    and the answer is more testing, not more budget (docs/agents.md) — the
    one argument where the Risk Officer and the Creative Architect agree.
    """
    if context.concentration is None:
        return (f"- This post's share of stage recent revenue: {UNAVAILABLE} "
                "(no recorded stage revenue to share)")
    line = f"- This post's share of stage recent revenue: **{context.concentration:.2%}**"
    if context.concentration > CONCENTRATION_THRESHOLD:
        line += (
            f" — **above the {CONCENTRATION_THRESHOLD:.0%} concentration "
            "threshold**: fragility; the answer is more testing, not more "
            "budget (§1)"
        )
    return line


def brief(action: Action, context: Optional[DebateContext] = None) -> str:
    """Render the Round 0 brief: shared, role-neutral, full of numbers.

    Every councillor receives exactly this brief — the entity, its metrics
    across both windows, the gate that fired with its evidence dict, the
    market baseline and its ``baseline_source``, the stage's spend and return,
    the account target, and the concentration figure (docs/debate-protocol.md
    §2). It names no role: asymmetric briefs produce arguments that look like
    disagreement but are actually different information, which is useless for
    adjudication.

    ``context`` omitted means the numbers are unavailable — the brief says so
    loudly instead of quietly degrading, because a brief without numbers is a
    known failure mode ("an agent arguing from adjectives has nothing to
    adjudicate", §5), not a formatting detail.
    """
    lines = [
        "# Moot debate brief — Round 0 (shared)",
        "",
        "Every councillor receives exactly this brief: no agent gets a fact "
        "the others do not (docs/debate-protocol.md §2, Round 0).",
        "",
    ]
    if context is None:
        lines += [
            "> ⚠ **NUMBERS UNAVAILABLE** — this brief was rendered without a "
            "DebateContext, so the entity metrics, market baseline, stage "
            "figures, account target and concentration are missing. A brief "
            "without numbers cannot be adjudicated: an agent arguing from "
            "adjectives has nothing to adjudicate (docs/debate-protocol.md "
            "§5). Build the context from the run state "
            "(`council.build_debate_context`).",
            "",
        ]
    lines += _action_lines(action)
    if context is not None:
        lines += ["", "## The entity", *_entity_lines(context)]
        lines += ["", *_metrics_table(context)]
        lines += ["", "## Market baseline (docs/gates.md §3)", *_baseline_lines(context)]
        lines += ["", "## The stage (recent window)",
                  f"- Stage recent spend: **{_money(context.stage_spend)}**",
                  f"- Stage recent return: **{_multiple(context.stage_return)}**"]
        lines += ["", "## The account target", *_target_lines(context)]
        lines += ["", "## Concentration", _concentration_line(context)]
    lines += ["", "## Gate evidence", *_evidence_lines(action)]
    lines += ["", "## Why this action is contested"]
    why = _borderline(action)
    lines.append(f"- {why}" if why is not None
                 else "- not marked contested by the runtime's borderline filter")
    return "\n".join(lines)


def charter_block(archetype: Archetype) -> str:
    """One archetype's full charter as markdown — the whole mandate, never a
    one-line paraphrase.

    docs/debate-protocol.md §5: similar-sounding openings mean the charters
    were under-loaded, so the brief passes everything the charter carries.
    """
    lines = [
        f"### Charter — {archetype.title} (`{archetype.id}`)",
        f"- **Mandate**: {archetype.mandate}",
        f"- **Watches**: {', '.join(archetype.primary_metrics)}",
        f"- **Always argues for**: "
        f"{', '.join(archetype.always_argues_for) or UNAVAILABLE}",
        f"- **Always argues against**: "
        f"{', '.join(archetype.always_argues_against) or UNAVAILABLE}",
        f"- **Hard veto**: {archetype.hard_veto or 'none — advisory'}",
        f"- **Blind spot**: {archetype.blind_spot}",
    ]
    return "\n".join(lines)
