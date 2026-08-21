"""Duplication pre-flight — docs/gates.md §9, mandatory before every §5/§6/§7
duplication. Failing either check means **do not create the ad**.

The idempotency key is the POST ID and only the post ID. Names drift —
suffixes get appended, sources get renamed, two genuinely different posts
share a name — so name-based dedupe fails in both directions at once
(docs/framework.md §4). No name comparison appears in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from agon.adapters.base import AdPlatformAdapter
from agon.config import Config, StageEntry
from agon.models import Ad, AdSet, Stage

OK = "ok"
ALREADY_PRESENT = "already-present"
BLOCKED_ROUTING = "blocked-routing"


@dataclass(frozen=True)
class PreflightResult:
    """The §9 verdict for one would-be duplication.

    ``status`` is one of ``ok`` | ``already-present`` | ``blocked-routing``.
    ``already-present`` carries the existing ad's ID and status — a PAUSED
    copy still skips (§9 A4): re-creating it silently overrides a decision
    someone already made; it is surfaced as a proposal instead.
    """

    status: str
    source_ad_id: str
    post_id: Optional[str]
    destination_stage: Stage
    destination_campaign_id: Optional[str] = None
    destination_adset_id: Optional[str] = None
    destination_adset_to_create: Optional[str] = None
    existing_ad_id: Optional[str] = None
    existing_ad_status: Optional[str] = None
    url_tags: Optional[str] = None
    reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == OK


def _cohort_adset_name(entry: StageEntry, now: datetime) -> Optional[str]:
    """The scale destination's current-month cohort ad set (§5 action):
    ``cohort_pattern`` like ``{yyyy}-{mm} winners`` rendered for this run."""
    if not entry.cohort_pattern:
        return None
    return entry.cohort_pattern.format(yyyy=now.strftime("%Y"), mm=now.strftime("%m"))


def preflight(
    adapter: AdPlatformAdapter,
    source_ad: Ad,
    destination_stage: Stage,
    adsets: list[AdSet],
    config: Config,
    now: datetime,
) -> PreflightResult:
    """Run §9 checks A (post-ID idempotency), B (market routing), C (url_tags).

    Market routing is DERIVED, never inferred: the source ad's campaign is
    looked up in the configured stage map and that market's destination is
    used. Name similarity is never consulted (framework.md §4).
    """
    base: dict[str, Any] = {
        "source_ad_id": source_ad.id,
        "post_id": source_ad.post_id,
        "destination_stage": destination_stage,
        "url_tags": source_ad.url_tags,
    }

    # --- B first: routing must resolve before enumeration is meaningful ---------
    source_entry = (
        config.locate_campaign(source_ad.campaign_id) if source_ad.campaign_id else None
    )
    if source_entry is None:
        return PreflightResult(
            status=BLOCKED_ROUTING,
            reasons=[
                f"source campaign {source_ad.campaign_id!r} is not in the stage "
                "map — market cannot be derived, only reported (§9 B)."
            ],
            evidence={
                **base,
                "source_campaign_id": source_ad.campaign_id,
                "check": "B-routing",
            },
            **base,
        )
    market = source_entry.market
    dest = config.destination_for(destination_stage, market)
    if dest is None:
        return PreflightResult(
            status=BLOCKED_ROUTING,
            reasons=[
                f"market {market} has no {destination_stage.value} destination "
                "campaign in the stage map — reported as a proposal, not "
                "duplicated (§9 B)."
            ],
            evidence={
                **base,
                "source_campaign_id": source_ad.campaign_id,
                "derived_market": market,
                "check": "B-routing",
            },
            **base,
        )

    # Resolve the destination ad set: the Reserve "Retired winners" set for
    # retirements, the current-month cohort for graduations (created if
    # absent, born PAUSED — §5 action).
    adset_to_create: Optional[str] = None
    if destination_stage is Stage.RESERVE:
        wanted = dest.adset_name or "Retired winners"
        dest_adset = next(
            (s for s in adsets if s.campaign_id == dest.campaign_id and s.name == wanted),
            None,
        )
        dest_adset_id = dest_adset.id if dest_adset else None
        if dest_adset_id is None:
            adset_to_create = wanted
    else:
        wanted = _cohort_adset_name(dest, now) or f"{now.strftime('%Y-%m')} winners"
        dest_adset = next(
            (s for s in adsets if s.campaign_id == dest.campaign_id and s.name == wanted),
            None,
        )
        dest_adset_id = dest_adset.id if dest_adset else None
        if dest_adset_id is None:
            adset_to_create = wanted

    # --- A: idempotency on post ID across the WHOLE destination campaign -------
    if source_ad.post_id is None:
        return PreflightResult(
            status=BLOCKED_ROUTING,
            reasons=[
                "source ad has no resolvable post ID — duplicating it would "
                "mint a fresh post and reset learning (framework.md §4)."
            ],
            evidence={**base, "derived_market": market, "check": "A-idempotency"},
            **base,
        )
    existing = adapter.list_ads_in_campaign(dest.campaign_id)
    for other in existing:
        if other.post_id != source_ad.post_id:
            continue
        if (other.status or "").upper() == "ARCHIVED":
            continue  # §9 A3: only non-ARCHIVED copies block
        paused = (other.status or "").upper() == "PAUSED"
        reason = (
            f"post {source_ad.post_id} already present in destination campaign "
            f"{dest.campaign_id} as ad {other.id} (status {other.status})."
        )
        if paused:
            # §9 A4: a paused copy still skips — re-creating it silently
            # overrides a decision someone already made. Surface as proposal.
            reason += (
                " A paused copy means a prior demotion or deliberate operator "
                "pause; surfaced as a proposal, not re-created."
            )
        return PreflightResult(
            status=ALREADY_PRESENT,
            destination_campaign_id=dest.campaign_id,
            destination_adset_id=dest_adset_id,
            existing_ad_id=other.id,
            existing_ad_status=other.status,
            reasons=[reason],
            evidence={
                **base,
                "derived_market": market,
                "destination_campaign_id": dest.campaign_id,
                "existing_ad_id": other.id,
                "existing_ad_status": other.status,
                "check": "A-idempotency",
            },
            **base,
        )

    # --- C: the tracking parameters travel with the copy (§9 C) ----------------
    if not source_ad.url_tags:
        # Not a blocker — but it must be visible in the evidence: a duplicate
        # without its UTM taxonomy is invisible to downstream analytics.
        note = "source carries no url_tags — copy will carry none either (§9 C)"
    else:
        note = f"url_tags carried: {source_ad.url_tags} (§9 C)"

    return PreflightResult(
        status=OK,
        destination_campaign_id=dest.campaign_id,
        destination_adset_id=dest_adset_id,
        destination_adset_to_create=adset_to_create,
        reasons=[f"pre-flight ok: {note}"],
        evidence={
            **base,
            "derived_market": market,
            "destination_campaign_id": dest.campaign_id,
            "destination_adset_id": dest_adset_id,
            "destination_adset_to_create": adset_to_create,
            "enumerated_ads": len(existing),
            "check": "A+B+C",
        },
        **base,
    )
