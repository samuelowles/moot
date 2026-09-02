"""Frozen data model for the whole runtime.

Every type here is immutable: gate results, actions and audits are compared and
replayed after the fact, and a mutable model lets a later stage of the pipeline
silently rewrite what an earlier gate decided. The vocabulary each field
carries is defined in docs/gates.md §1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Stage(str, Enum):
    """The ladder stages (docs/framework.md §1)."""

    PROVING = "PROVING"
    SCALE = "SCALE"
    RESERVE = "RESERVE"
    RETARGET = "RETARGET"

    @classmethod
    def from_config_key(cls, key: str) -> "Stage":
        """Map a lowercase config key like ``proving`` to its enum member."""
        try:
            return cls(key.upper())
        except ValueError:
            raise ValueError(f"unknown stage {key!r}") from None


class Decision(str, Enum):
    """The transitions a gate can propose (docs/framework.md §2)."""

    KILL = "KILL"
    GRADUATE = "GRADUATE"
    FATIGUE = "FATIGUE"
    DEMOTE = "DEMOTE"
    BUDGET_UP = "BUDGET_UP"
    BUDGET_DOWN = "BUDGET_DOWN"
    RECOVER = "RECOVER"
    WATCH = "WATCH"
    NONE = "NONE"


class CreativeType(str, Enum):
    """Creative families. Hook rate is defined for VIDEO only (§11.5)."""

    VIDEO = "VIDEO"
    STATIC = "STATIC"
    CAROUSEL = "CAROUSEL"

    @classmethod
    def parse(cls, value: Optional[str]) -> "CreativeType":
        """Coerce a platform string, defaulting to STATIC when unresolvable.

        Meta reports creative type indirectly (video fields present/absent,
        ``template`` metadata, asset shape). Anything the parser cannot identify
        as VIDEO or CAROUSEL is treated as STATIC — the conservative choice,
        because the only privilege STATIC grants is *exemption* from a gate, and
        a video misread as static skips a hook gate rather than failing an ad
        on a fabricated zero.
        """
        if value is None:
            return cls.STATIC
        normalized = value.strip().upper()
        if normalized in {"VIDEO", "VID"}:
            return cls.VIDEO
        if normalized in {"CAROUSEL", "CAR"}:
            return cls.CAROUSEL
        return cls.STATIC


@dataclass(frozen=True)
class Metrics:
    """One window's numbers for one entity (docs/gates.md §1).

    Every field is Optional because absence is meaningful: a missing purchase
    action means *absent from this response*, not zero (§11.2). Derived
    properties therefore return ``None`` — never ``0.0``, never an exception —
    when an input is missing or a denominator is zero. A synthesised zero is a
    fabricated fact, and gates act on it as if it were real.
    """

    spend: Optional[float] = None
    impressions: Optional[int] = None
    clicks: Optional[int] = None
    outbound_clicks: Optional[int] = None
    outbound_ctr: Optional[float] = None  # decimal fraction already — §11.4
    cpm: Optional[float] = None
    frequency: Optional[float] = None
    purchases: Optional[int] = None
    purchase_value: Optional[float] = None
    carts: Optional[int] = None
    video_3s: Optional[int] = None  # absent for non-video creative — §11.5
    thruplays: Optional[int] = None

    @property
    def return_(self) -> Optional[float]:
        """Attributed conversion value ÷ spend (§1 ``return``)."""
        if self.purchase_value is None or self.spend in (None, 0):
            return None
        return self.purchase_value / self.spend

    @property
    def cost_per_cart(self) -> Optional[float]:
        """Spend ÷ add-to-carts. The primary Proving Ground efficiency metric."""
        if self.spend is None or self.carts in (None, 0):
            return None
        return self.spend / self.carts

    @property
    def cost_per_acquisition(self) -> Optional[float]:
        """Spend ÷ purchases. Reported, rarely gated on directly (§1)."""
        if self.spend is None or self.purchases in (None, 0):
            return None
        return self.spend / self.purchases

    @property
    def aov(self) -> Optional[float]:
        """Conversion value ÷ purchases (§1 ``aov``)."""
        if self.purchase_value is None or self.purchases in (None, 0):
            return None
        return self.purchase_value / self.purchases

    @property
    def hook_rate(self) -> Optional[float]:
        """3-second views ÷ impressions.

        Undefined for static creative: non-video ads never carry video actions
        (§11.5), so ``video_3s`` is ``None`` and this property returns ``None``
        — never ``0.0``. Gates consuming hook rate must skip rather than fail
        the ad.
        """
        if self.video_3s is None or self.impressions in (None, 0):
            return None
        return self.video_3s / self.impressions

    @property
    def hold_rate(self) -> Optional[float]:
        """Thruplays ÷ 3-second views (§1 ``hold_rate``)."""
        if self.thruplays is None or self.video_3s in (None, 0):
            return None
        return self.thruplays / self.video_3s

    @property
    def cart_rate(self) -> Optional[float]:
        """Add-to-carts ÷ outbound clicks (§1 ``cart_rate``)."""
        if self.carts is None or self.outbound_clicks in (None, 0):
            return None
        return self.carts / self.outbound_clicks


@dataclass(frozen=True)
class Campaign:
    """A campaign in the stage map, with lifetime aggregate metrics.

    Creative-level facts (post id, url_tags, destination) live on :class:`Ad`
    — a campaign is a budget container, and pretending it carries a single
    creative's facts invites acting on numbers that were never reported.
    """

    id: str
    name: str
    status: Optional[str] = None
    effective_status: Optional[str] = None
    account_id: Optional[str] = None  # parent id
    market: Optional[str] = None
    stage: Optional[Stage] = None
    age_days: Optional[int] = None
    recent: Optional[Metrics] = None
    trailing: Optional[Metrics] = None
    lifetime: Optional[Metrics] = None
    daily_budget: Optional[float] = None


@dataclass(frozen=True)
class AdSet:
    """An ad set. Baseline candidates are ad sets (docs/gates.md §3).

    Like campaigns, an ad set is a container: its creative facts are on the
    ads it holds, never synthesised here.
    """

    id: str
    name: str
    status: Optional[str] = None
    effective_status: Optional[str] = None
    campaign_id: Optional[str] = None  # parent id
    market: Optional[str] = None
    stage: Optional[Stage] = None
    age_days: Optional[int] = None
    recent: Optional[Metrics] = None
    trailing: Optional[Metrics] = None
    lifetime: Optional[Metrics] = None
    daily_budget: Optional[float] = None


@dataclass(frozen=True)
class Ad:
    """An ad: the unit every gate in §4–§7 evaluates."""

    id: str
    name: str
    status: Optional[str] = None
    effective_status: Optional[str] = None
    adset_id: Optional[str] = None  # parent id
    campaign_id: Optional[str] = None
    market: Optional[str] = None
    stage: Optional[Stage] = None
    creative_type: CreativeType = CreativeType.STATIC
    post_id: Optional[str] = None
    url_tags: Optional[str] = None
    destination_url: Optional[str] = None
    age_days: Optional[int] = None
    recent: Optional[Metrics] = None
    trailing: Optional[Metrics] = None
    lifetime: Optional[Metrics] = None


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict on one entity.

    ``reasons`` are human-readable and land verbatim in the run report;
    ``evidence`` is numeric and lands in the audit JSONL, so every threshold
    comparison can be re-checked after the fact. ``suppressed_by`` is set by
    the pipeline on the *winning* result and names the losing gate when §12
    precedence rules collide — the suppressed decision stays visible (§12).
    """

    decision: Decision
    entity_id: str
    reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    suppressed_by: Optional[Decision] = None

    def with_suppressed(self, loser: Decision) -> "GateResult":
        """Return a copy recording that ``loser`` was ruled out by precedence."""
        return GateResult(
            decision=self.decision,
            entity_id=self.entity_id,
            reasons=list(self.reasons),
            evidence=dict(self.evidence),
            suppressed_by=loser,
        )


@dataclass(frozen=True)
class Action:
    """A write the pipeline intends to perform, before the safety layer looks.

    ``authorized`` reflects the configured envelope (examples/config.example.yaml
    ``envelope:``); the write layer re-checks it at dispatch. ``source_gate``
    ties the action back to the decision that produced it for the audit trail.
    """

    verb: str
    target_id: str
    params: dict[str, Any] = field(default_factory=dict)
    authorized: bool = True
    rationale: str = ""
    source_gate: Optional[Decision] = None
    #: The numeric evidence from the GateResult that produced this action.
    #: It rides along for two reasons: the audit entry records why a write
    #: happened (docs/writes.md mechanism 10), and council.contested() reads it
    #: to decide whether the call was close enough to be worth arguing about.
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_proposal(self, rationale: str) -> "Action":
        """Return a copy downgraded to an unauthorized proposal."""
        return Action(
            verb=self.verb,
            target_id=self.target_id,
            params=dict(self.params),
            authorized=False,
            rationale=rationale,
            source_gate=self.source_gate,
            evidence=dict(self.evidence),
        )
