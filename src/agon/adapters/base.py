"""The platform adapter protocol: every read and write Agon can perform.

Reads feed the pipeline; writes are the only way the system touches the
account, and every write method takes ``dry_run`` and ``validate_only`` so
the safety layer (writes.py) can rehearse and pre-validate each action
without exception paths of its own.

There is no delete verb anywhere in this protocol — retirement is always a
pause, because the entity ID and its lifetime metrics are the audit anchor
(docs/framework.md §6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agon.models import Ad, AdSet, Campaign


class AdapterError(Exception):
    """Base class for adapter failures."""


class IncompletePullError(AdapterError):
    """A read returned fewer rows than paging promised (§11.6).

    Raised rather than returning a short result: analysing page one and
    presenting it as complete is the classic silent failure. The pipeline
    converts this into §10 circuit breaker 2 — no writes this run.
    """


class PostIdMismatchError(AdapterError):
    """A duplicated ad did not keep its source post ID (framework.md §4).

    The whole point of duplicating by post ID is inheriting the accumulated
    social proof. A copy with a fresh post is a new ad with zero history, so
    the chain re-reads and verifies, and fails loudly when the ID did not
    survive.
    """


class WriteRefusedError(AdapterError):
    """A write was refused before dispatch (wrong account, failed validation)."""


@dataclass(frozen=True)
class EntitySnapshot:
    """The full object graph for one run, with pull provenance."""

    account_id: str
    campaigns: list[Campaign] = field(default_factory=list)
    adsets: list[AdSet] = field(default_factory=list)
    ads: list[Ad] = field(default_factory=list)
    pull_complete: bool = True
    errors: list[str] = field(default_factory=list)


@runtime_checkable
class AdPlatformAdapter(Protocol):
    """Everything Agon can ask the platform to do."""

    # --- reads -----------------------------------------------------------------
    def fetch_entities(self) -> EntitySnapshot:
        """Campaigns, ad sets and ads with recent/trailing/lifetime metrics."""

    def fetch_insights(self, entity_id: str, window: str) -> list[dict[str, Any]]:
        """Raw platform insight rows for one entity and window preset."""

    def get_ad(self, ad_id: str) -> Ad:
        """One ad, with its creative-resolved post ID."""

    def list_ads_in_campaign(self, campaign_id: str) -> list[Ad]:
        """EVERY ad in a campaign — all ad sets, all statuses, paginated to
        exhaustion. This is what §9 A idempotency enumerates against."""

    # --- writes (all rehearseable; none of them delete) --------------------------
    def create_creative_from_post(
        self,
        act_id: str,
        page_id: str,
        post_id: str,
        url_tags: str | None,
        *,
        dry_run: bool,
        validate_only: bool,
    ) -> str:
        """POST /{act}/adcreatives referencing object_story_id (framework §4)."""

    def create_ad(
        self,
        act_id: str,
        adset_id: str,
        creative_id: str,
        name: str,
        status: str = "PAUSED",
        url_tags: str | None = None,
        *,
        dry_run: bool,
        validate_only: bool,
    ) -> str:
        """POST /{act}/ads — born PAUSED, activated only after verification."""

    def set_status(
        self,
        entity_id: str,
        entity_type: str,
        status: str,
        *,
        dry_run: bool,
        validate_only: bool,
    ) -> None:
        """PAUSE or ACTIVE. PAUSE is the only retirement that exists."""

    def set_campaign_budget(
        self,
        campaign_id: str,
        daily_amount: float,
        *,
        dry_run: bool,
        validate_only: bool,
    ) -> None:
        """Scale-stage campaign budget only (§8); the write layer caps steps."""

    def create_adset(
        self,
        act_id: str,
        campaign_id: str,
        name: str,
        pixel_id: str,
        *,
        status: str = "PAUSED",
        dry_run: bool,
        validate_only: bool,
    ) -> str:
        """Create a cohort/Reserve ad set — born PAUSED (§5 action)."""
