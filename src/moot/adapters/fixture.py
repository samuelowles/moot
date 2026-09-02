"""Fixture adapter — reads JSON from a directory; writes are recorded, never
dispatched.

Tests (and `moot plan --adapter fixture`) run entirely against this backend:
no network, no account. The fixture JSON passes through the same
``parse_insights_row`` traps as live Meta data, so a fixture row exercises
exactly the parsing the runtime performs in production.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from moot.adapters.base import (
    EntitySnapshot,
    EntityType,
    IncompletePullError,
    PostIdMismatchError,
)
from moot.metrics import parse_insights_row
from moot.models import Ad, AdSet, Campaign, CreativeType, Metrics, Stage

# windows the fixture directory may describe, mapped to Metrics slots
_WINDOWS = ("recent", "trailing", "lifetime")


def _metrics_for(entity_json: dict[str, Any], window: str) -> Optional[Metrics]:
    row = (entity_json.get("insights") or {}).get(window)
    return parse_insights_row(row) if row is not None else None


def _stage(raw: Optional[str]) -> Optional[Stage]:
    return Stage.from_config_key(raw) if raw else None


def _campaign(c: dict[str, Any], account_id: Optional[str]) -> Campaign:
    """One campaign from its fixture JSON row."""
    return Campaign(
        id=str(c["id"]),
        name=str(c.get("name", "")),
        status=c.get("status"),
        effective_status=c.get("effective_status") or c.get("status"),
        account_id=account_id,
        market=c.get("market"),
        stage=_stage(c.get("stage")),
        daily_budget=c.get("daily_budget"),
        recent=_metrics_for(c, "recent"),
        trailing=_metrics_for(c, "trailing"),
        lifetime=_metrics_for(c, "lifetime"),
    )


def _adset(s: dict[str, Any]) -> AdSet:
    """One ad set from its fixture JSON row."""
    return AdSet(
        id=str(s["id"]),
        name=str(s.get("name", "")),
        status=s.get("status"),
        effective_status=s.get("effective_status") or s.get("status"),
        campaign_id=s.get("campaign_id"),
        market=s.get("market"),
        stage=_stage(s.get("stage")),
        daily_budget=s.get("daily_budget"),
        recent=_metrics_for(s, "recent"),
        trailing=_metrics_for(s, "trailing"),
        lifetime=_metrics_for(s, "lifetime"),
    )


def _ad(a: dict[str, Any]) -> Ad:
    """One ad from its fixture JSON row — the single mapping both the pull
    and the post-write re-read perform, so a verify read sees exactly what
    the pull saw."""
    return Ad(
        id=str(a["id"]),
        name=str(a.get("name", "")),
        status=a.get("status"),
        effective_status=a.get("effective_status") or a.get("status"),
        adset_id=a.get("adset_id"),
        campaign_id=a.get("campaign_id"),
        market=a.get("market"),
        stage=_stage(a.get("stage")),
        creative_type=CreativeType.parse(a.get("creative_type")),
        post_id=a.get("post_id"),
        url_tags=a.get("url_tags"),
        destination_url=a.get("destination_url"),
        age_days=a.get("age_days"),
        recent=_metrics_for(a, "recent"),
        trailing=_metrics_for(a, "trailing"),
        lifetime=_metrics_for(a, "lifetime"),
    )


class FixtureAdapter:
    """An in-memory account loaded from a directory of JSON files.

    ``entities.json`` holds the object graph; each entity may carry an
    ``insights`` mapping of ``{recent, trailing, lifetime}`` raw rows. Set
    ``"pull_complete": false`` on the root to rehearse §10 breaker 2.
    """

    def __init__(self, fixtures_dir: str | Path):
        self.root = Path(fixtures_dir)
        entities_path = self.root / "entities.json"
        if not entities_path.exists():
            raise FileNotFoundError(
                f"fixture directory {self.root} has no entities.json"
            )
        self._data: dict[str, Any] = json.loads(entities_path.read_text(encoding="utf-8"))
        self.writes: list[dict[str, Any]] = []
        self._sequence = 0

    # --- fixture-only helpers ---------------------------------------------------

    @property
    def pull_complete(self) -> bool:
        """The fixture's §10 breaker-2 rehearsal switch."""
        return bool(self._data.get("pull_complete", True))

    def _next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}_{self._sequence}"

    def _record(self, method: str, **params: Any) -> None:
        self.writes.append({"method": method, **params})

    # --- reads --------------------------------------------------------------------

    def fetch_entities(self) -> EntitySnapshot:
        """The whole graph from entities.json, parsing traps included (§11)."""
        campaigns = [
            _campaign(c, self._data.get("account_id"))
            for c in self._data.get("campaigns", [])
        ]
        adsets = [_adset(s) for s in self._data.get("adsets", [])]
        ads = [_ad(a) for a in self._data.get("ads", [])]
        return EntitySnapshot(
            account_id=str(self._data.get("account_id", "")),
            campaigns=campaigns,
            adsets=adsets,
            ads=ads,
            pull_complete=self.pull_complete,
            errors=list(self._data.get("errors", [])),
        )

    def fetch_insights(self, entity_id: str, window: str) -> list[dict[str, Any]]:
        """Raw stored rows; a missing entity is an incomplete pull, not empty."""
        if window not in _WINDOWS:
            raise ValueError(f"unknown window {window!r}; expected one of {_WINDOWS}")
        for kind in ("campaigns", "adsets", "ads"):
            for entity in self._data.get(kind, []):
                if str(entity.get("id")) == entity_id:
                    row = (entity.get("insights") or {}).get(window)
                    return [row] if row is not None else []
        # §10 breaker 2 rehearsal: a missing entity is an incomplete pull, not
        # an empty result.
        raise IncompletePullError(
            f"fixture has no entity {entity_id!r} — treating as incomplete pull"
        )

    def get_ad(self, ad_id: str) -> Ad:
        """The same mapping the pull performs, so read-backs are honest."""
        for a in self._data.get("ads", []):
            if str(a.get("id")) == ad_id:
                return _ad(a)
        raise KeyError(f"fixture has no ad {ad_id!r}")

    def get_adset(self, adset_id: str) -> AdSet:
        """One ad set — the mechanism-9 read-back for ad-set status writes.

        Built by the same mapping the pull uses, so a verify read sees
        exactly what the pull saw."""
        for s in self._data.get("adsets", []):
            if str(s.get("id")) == adset_id:
                return _adset(s)
        raise KeyError(f"fixture has no ad set {adset_id!r}")

    def get_campaign(self, campaign_id: str) -> Campaign:
        """One campaign — the mechanism-9 read-back for budget writes.

        Built by the same mapping the pull uses, so a verify read sees
        exactly what the pull saw."""
        for c in self._data.get("campaigns", []):
            if str(c.get("id")) == campaign_id:
                return _campaign(c, self._data.get("account_id"))
        raise KeyError(f"fixture has no campaign {campaign_id!r}")

    def list_ads_in_campaign(self, campaign_id: str) -> list[Ad]:
        """Every ad in the campaign — all ad sets, all statuses (§9 A)."""
        matching = [
            a for a in self._data.get("ads", []) if a.get("campaign_id") == campaign_id
        ]
        if self._data.get("campaign_page_truncated"):
            # Fixture hook: simulate the §11.6 trap where page one is not the
            # whole story. The adapter must surface this, never hide it.
            raise IncompletePullError(
                f"campaign {campaign_id}: fixture marks enumeration truncated"
            )
        return [self.get_ad(str(a["id"])) for a in matching]

    # --- writes: recorded, never dispatched ------------------------------------------

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
        """Recorded only — the fixture never dispatches anything."""
        creative_id = self._next_id("cr")
        self._record(
            "create_creative_from_post",
            act_id=act_id,
            page_id=page_id,
            post_id=post_id,
            url_tags=url_tags,
            dry_run=dry_run,
            validate_only=validate_only,
        )
        return creative_id

    def create_ad(
        self,
        act_id: str,
        adset_id: str,
        creative_id: str,
        name: str,
        status: str = "PAUSED",
        url_tags: str | None = None,
        post_id: str | None = None,
        *,
        dry_run: bool,
        validate_only: bool,
    ) -> str:
        """Registers the created ad so the verify re-read can find it."""
        ad_id = self._next_id("ad")
        self._record(
            "create_ad",
            act_id=act_id,
            adset_id=adset_id,
            creative_id=creative_id,
            name=name,
            status=status,
            url_tags=url_tags,
            dry_run=dry_run,
            validate_only=validate_only,
        )
        if not (dry_run or validate_only):
            # Register the created ad so the verification re-read can find it.
            # ``post_id`` travels with the creative in the real chain; the
            # fixture threads it explicitly so the verify step is honest.
            self._data.setdefault("ads", []).append(
                {
                    "id": ad_id,
                    "name": name,
                    "status": status,
                    "effective_status": status,
                    "adset_id": adset_id,
                    "campaign_id": self._campaign_of_adset(adset_id),
                    "market": None,
                    "creative_type": "VIDEO",
                    "post_id": post_id,
                    "url_tags": url_tags,
                }
            )
        return ad_id

    def _campaign_of_adset(self, adset_id: str) -> Optional[str]:
        for s in self._data.get("adsets", []):
            if str(s.get("id")) == adset_id:
                return s.get("campaign_id")
        return None

    def set_status(
        self,
        entity_id: str,
        entity_type: EntityType,
        status: str,
        *,
        dry_run: bool,
        validate_only: bool,
    ) -> None:
        """Records AND applies the change, so a read-back contradicts
        only when a real write would."""
        self._record(
            "set_status",
            entity_id=entity_id,
            entity_type=entity_type,
            status=status,
            dry_run=dry_run,
            validate_only=validate_only,
        )
        if not (dry_run or validate_only):
            for kind in ("campaigns", "adsets", "ads"):
                for entity in self._data.get(kind, []):
                    if str(entity.get("id")) == entity_id:
                        entity["status"] = status
                        entity["effective_status"] = status

    def set_campaign_budget(
        self,
        campaign_id: str,
        daily_amount: float,
        *,
        dry_run: bool,
        validate_only: bool,
    ) -> None:
        """Applies the new budget so the §8 read-back is honest."""
        self._record(
            "set_campaign_budget",
            campaign_id=campaign_id,
            daily_amount=daily_amount,
            dry_run=dry_run,
            validate_only=validate_only,
        )
        if not (dry_run or validate_only):
            for c in self._data.get("campaigns", []):
                if str(c.get("id")) == campaign_id:
                    c["daily_budget"] = daily_amount

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
        """Born PAUSED in the in-memory graph, pixel set explicitly."""
        adset_id = self._next_id("adset")
        self._record(
            "create_adset",
            act_id=act_id,
            campaign_id=campaign_id,
            name=name,
            pixel_id=pixel_id,
            status=status,
            dry_run=dry_run,
            validate_only=validate_only,
        )
        if not (dry_run or validate_only):
            self._data.setdefault("adsets", []).append(
                {"id": adset_id, "name": name, "status": status, "campaign_id": campaign_id}
            )
        return adset_id

    # --- the framework.md §4 duplication chain ----------------------------------------

    def duplicate_post(
        self,
        source_ad: Ad,
        act_id: str,
        page_id: str,
        dest_adset_id: str,
        name: str,
        *,
        dry_run: bool,
        validate_only: bool,
    ) -> str:
        """creative → ad (PAUSED) → re-read and verify the post ID survived.

        The chain every duplication runs (framework.md §4). The fixture
        threads the source post ID through its registry so the verification
        re-read is a real check, not a tautology.
        """
        creative_id = self.create_creative_from_post(
            act_id,
            page_id,
            source_ad.post_id or "",
            source_ad.url_tags,
            dry_run=dry_run,
            validate_only=validate_only,
        )
        new_ad_id = self.create_ad(
            act_id,
            dest_adset_id,
            creative_id,
            name,
            status="PAUSED",  # born PAUSED — activated only after verification (§5)
            url_tags=source_ad.url_tags,
            post_id=source_ad.post_id,
            dry_run=dry_run,
            validate_only=validate_only,
        )
        if dry_run or validate_only:
            return new_ad_id
        # Re-read and verify the post ID survived (framework.md §4).
        copy = self.get_ad(new_ad_id)
        if copy.post_id != source_ad.post_id:
            raise PostIdMismatchError(
                f"duplicated ad {new_ad_id} carries post {copy.post_id!r}, "
                f"expected {source_ad.post_id!r}"
            )
        return new_ad_id
