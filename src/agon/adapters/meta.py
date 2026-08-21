"""Live Meta Graph API adapter.

Every read paginates ``paging.cursors.after`` to exhaustion and raises
:class:`IncompletePullError` rather than returning a short result — analysing
page one and presenting it as complete is the classic silent failure
(docs/gates.md §11.6). Every write first issues the same call with
``execution_options=["validate_only"]`` and aborts on failure, then refuses
any account not in the configured allowlist. There is no delete verb here and
never will be (docs/framework.md §6).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import requests

from agon.adapters.base import (
    AdapterError,
    EntitySnapshot,
    IncompletePullError,
    PostIdMismatchError,
    WriteRefusedError,
)
from agon.metrics import parse_insights_row
from agon.models import Ad, AdSet, Campaign, CreativeType, Metrics

logger = logging.getLogger(__name__)

DEFAULT_GRAPH_VERSION = "v23.0"

AD_FIELDS = (
    "id,name,status,effective_status,adset_id,campaign_id,"
    "creative{effective_object_story_id,url_tags}"
)
ADSET_FIELDS = "id,name,status,effective_status,campaign_id,daily_budget"
CAMPAIGN_FIELDS = "id,name,status,effective_status,account_id,daily_budget"

# §11.8 — attribution window held constant across every comparison in a run.
ATTRIBUTION_WINDOWS = ["7d_click", "1d_view"]


def _post_id_from_story_id(story_id: Optional[str]) -> Optional[str]:
    """``effective_object_story_id`` arrives as ``{page_id}_{post_id}``.

    framework.md §4: the post ID is the unit of the pipeline — strip the page
    prefix so dedupe keys are stable across pages.
    """
    if not story_id or "_" not in story_id:
        return story_id
    return story_id.split("_", 1)[1]


class MetaAdapter:
    """Graph API client. Bearer token comes from ``META_ACCESS_TOKEN``."""

    def __init__(
        self,
        allowed_account_ids: tuple[str, ...] = (),
        graph_version: str = DEFAULT_GRAPH_VERSION,
        window_presets: Optional[dict[str, str]] = None,
        session: Optional[requests.Session] = None,
    ):
        self.allowed_account_ids = tuple(allowed_account_ids)
        self.graph_version = graph_version
        # Named presets only — §11.7 forbids combining an explicit range with
        # a preset, so the runtime never builds time_range dicts at all.
        self.window_presets = {
            "recent": "last_7d",
            "trailing": "last_30d",
            "lifetime": "lifetime",
        }
        if window_presets:
            self.window_presets.update(window_presets)
        self._session = session or requests.Session()

    # --- transport -----------------------------------------------------------

    @property
    def _token(self) -> str:
        token = os.environ.get("META_ACCESS_TOKEN", "")
        if not token:
            raise AdapterError(
                "META_ACCESS_TOKEN is not set — the live adapter refuses to "
                "guess credentials"
            )
        return token

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self._session.get(
            f"https://graph.facebook.com/{self.graph_version}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=60,
        )
        payload = response.json() if response.content else {}
        if response.status_code != 200 or "error" in payload:
            raise AdapterError(f"GET {path} failed: {payload.get('error', response.status_code)}")
        return payload

    def _get_all_pages(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Follow ``paging.cursors.after`` to exhaustion (§11.6).

        Raises :class:`IncompletePullError` when paging signals more data but
        the cursor chain breaks — a short result must never masquerade as the
        complete set.
        """
        rows: list[dict[str, Any]] = []
        after: Optional[str] = None
        while True:
            page_params = dict(params)
            if after is not None:
                page_params["after"] = after
            payload = self._get(path, page_params)
            rows.extend(payload.get("data", []))
            paging = payload.get("paging") or {}
            cursors = paging.get("cursors") or {}
            after = cursors.get("after")
            if "next" in paging and not after:
                raise IncompletePullError(
                    f"GET {path}: paging advertises a next page but no cursor — "
                    "inconsistent pagination, refusing to return a short result"
                )
            if not after:
                return rows

    def _post(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """POST with a validate_only rehearsal first; abort on failure."""
        self._post_raw(path, {**params, "execution_options": ["validate_only"]})
        return self._post_raw(path, params)

    def _post_raw(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self._session.post(
            f"https://graph.facebook.com/{self.graph_version}/{path}",
            data=params,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=60,
        )
        payload = response.json() if response.content else {}
        if response.status_code != 200 or "error" in payload:
            raise WriteRefusedError(
                f"POST {path} failed: {payload.get('error', response.status_code)}"
            )
        return payload


    def _rehearse(self, path: str, params: dict[str, Any]) -> None:
        """Issue the same write with execution_options=["validate_only"] first.

        A failed rehearsal aborts the real write: Graph validation errors are
        cheap to discover before the account is touched.
        """
        self._post_raw(path, {**params, "execution_options": ["validate_only"]})

    def _check_account(self, act_id: str) -> None:
        if not self.allowed_account_ids:
            raise WriteRefusedError(
                "no allowed account ids configured — Agon refuses every write"
            )
        if act_id not in self.allowed_account_ids:
            raise WriteRefusedError(
                f"account {act_id} is not in allowed_account_ids "
                f"({', '.join(self.allowed_account_ids)}) — write refused"
            )

    def _account_of(self, entity_id: str, entity_type: str) -> str:
        """Resolve the owning account of an entity before mutating it."""
        if entity_type == "campaign":
            fields = "account_id"
        else:
            # ad sets carry account_id directly; ads resolve through the ad set
            fields = "account_id" if entity_type == "adset" else "adset{account_id}"
        payload = self._get(entity_id, {"fields": fields})
        if entity_type == "ad":
            account = ((payload.get("adset") or {}).get("account_id")) or ""
        else:
            account = payload.get("account_id") or ""
        if not account:
            raise WriteRefusedError(
                f"could not resolve the owning account of {entity_id} — write refused"
            )
        return account

    # --- reads ------------------------------------------------------------------

    def _insights(self, entity_id: str, window: str) -> Optional[Metrics]:
        preset = self.window_presets.get(window)
        if preset is None:
            raise ValueError(f"unknown window {window!r}")
        params = {
            "time_preset": preset,  # named preset only — §11.7
            "action_attribution_windows": json.dumps(ATTRIBUTION_WINDOWS),  # §11.8
            "fields": (
                "spend,impressions,clicks,cpm,frequency,outbound_clicks,"
                "outbound_clicks_ctr,actions,action_values,"
                "video_3s_views,video_thruplay_watched_actions"
            ),
        }
        rows = self._get_all_pages(f"{entity_id}/insights", params)
        if not rows:
            return None
        return parse_insights_row(rows[0])

    def fetch_entities(self) -> EntitySnapshot:
        account_id = (
            self.allowed_account_ids[0] if self.allowed_account_ids else ""
        )
        if not account_id:
            raise AdapterError(
                "fetch_entities needs the account id — pass allowed_account_ids"
            )
        pull_errors: list[str] = []
        campaigns: list[Campaign] = []
        adsets: list[AdSet] = []
        ads: list[Ad] = []
        try:
            campaign_rows = self._get_all_pages(
                f"{account_id}/campaigns", {"fields": CAMPAIGN_FIELDS, "limit": 100}
            )
            adset_rows = self._get_all_pages(
                f"{account_id}/adsets", {"fields": ADSET_FIELDS, "limit": 100}
            )
            ad_rows = self._get_all_pages(
                f"{account_id}/ads", {"fields": AD_FIELDS, "limit": 100}
            )
        except (AdapterError, IncompletePullError) as exc:
            # §10 breaker 2: surface the gap; never present a partial pull.
            return EntitySnapshot(
                account_id=account_id,
                pull_complete=False,
                errors=[str(exc)],
            )
        for row in campaign_rows:
            campaigns.append(
                Campaign(
                    id=row["id"],
                    name=row.get("name", ""),
                    status=row.get("status"),
                    effective_status=row.get("effective_status"),
                    account_id=row.get("account_id"),
                    daily_budget=_as_float(row.get("daily_budget")),
                    recent=self._insights(row["id"], "recent"),
                    trailing=self._insights(row["id"], "trailing"),
                    lifetime=self._insights(row["id"], "lifetime"),
                )
            )
        for row in adset_rows:
            adsets.append(
                AdSet(
                    id=row["id"],
                    name=row.get("name", ""),
                    status=row.get("status"),
                    effective_status=row.get("effective_status"),
                    campaign_id=row.get("campaign_id"),
                    daily_budget=_as_float(row.get("daily_budget")),
                    recent=self._insights(row["id"], "recent"),
                    trailing=self._insights(row["id"], "trailing"),
                    lifetime=self._insights(row["id"], "lifetime"),
                )
            )
        for row in ad_rows:
            ads.append(self._ad_from_row(row))
        return EntitySnapshot(
            account_id=account_id,
            campaigns=campaigns,
            adsets=adsets,
            ads=ads,
            pull_complete=not pull_errors,
            errors=pull_errors,
        )

    def _ad_from_row(self, row: dict[str, Any]) -> Ad:
        creative = row.get("creative") or {}
        story_id = creative.get("effective_object_story_id")
        recent = self._insights(row["id"], "recent")
        trailing = self._insights(row["id"], "trailing")
        # Creative-type inference per §11.5: static creative has no video
        # actions, so video data in either window means video creative. A
        # misread lands conservatively (STATIC skips hook gates rather than
        # failing an ad on a fabricated zero).
        has_video_actions = any(
            m is not None and m.video_3s is not None for m in (recent, trailing)
        )
        return Ad(
            id=row["id"],
            name=row.get("name", ""),
            status=row.get("status"),
            effective_status=row.get("effective_status"),
            adset_id=row.get("adset_id"),
            campaign_id=row.get("campaign_id"),
            post_id=_post_id_from_story_id(story_id),
            url_tags=creative.get("url_tags"),
            creative_type=CreativeType.VIDEO if has_video_actions else CreativeType.STATIC,
            recent=recent,
            trailing=trailing,
            lifetime=self._insights(row["id"], "lifetime"),
        )

    def fetch_insights(self, entity_id: str, window: str) -> list[dict[str, Any]]:
        preset = self.window_presets.get(window)
        if preset is None:
            raise ValueError(f"unknown window {window!r}")
        return self._get_all_pages(
            f"{entity_id}/insights",
            {
                "time_preset": preset,
                "action_attribution_windows": json.dumps(ATTRIBUTION_WINDOWS),
                "fields": "spend,impressions,clicks,cpm,frequency,actions,action_values",
            },
        )

    def get_ad(self, ad_id: str) -> Ad:
        row = self._get(ad_id, {"fields": AD_FIELDS})
        return self._ad_from_row(row)

    def list_ads_in_campaign(self, campaign_id: str) -> list[Ad]:
        """Every ad in the campaign — all ad sets, all statuses (§9 A)."""
        rows = self._get_all_pages(
            f"{campaign_id}/ads", {"fields": AD_FIELDS, "limit": 100}
        )
        return [self._ad_from_row(row) for row in rows]

    # --- writes (validate_only rehearsal + allowlist on every one) -----------------

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
        self._check_account(act_id)
        object_story_id = f"{page_id}_{post_id}" if post_id else page_id
        params: dict[str, Any] = {"object_story_id": object_story_id}
        if url_tags:
            # §9 C — carry the tracking parameters: a duplicate that loses its
            # UTM taxonomy is invisible to every downstream analytics surface.
            params["url_tags"] = url_tags
        if dry_run or validate_only:
            self._rehearse(f"{act_id}/adcreatives", params)
            return "dry-run"
        result = self._post(f"{act_id}/adcreatives", params)
        return str(result.get("id", ""))

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
        self._check_account(act_id)
        params: dict[str, Any] = {
            "name": name,
            "adset_id": adset_id,
            "creative": json.dumps({"creative_id": creative_id}),
            "status": status,  # born PAUSED — framework.md §4
        }
        if url_tags:
            params["url_tags"] = url_tags
        if dry_run or validate_only:
            self._rehearse(f"{act_id}/ads", params)
            return "dry-run"
        result = self._post(f"{act_id}/ads", params)
        return str(result.get("id", ""))

    def set_status(
        self,
        entity_id: str,
        entity_type: str,
        status: str,
        *,
        dry_run: bool,
        validate_only: bool,
    ) -> None:
        if status not in ("ACTIVE", "PAUSED"):
            raise WriteRefusedError(f"unsupported status {status!r}")
        account = self._account_of(entity_id, entity_type)
        self._check_account(account)
        params: dict[str, Any] = {"status": status}
        if dry_run or validate_only:
            self._post_raw(entity_id, {**params, "execution_options": ["validate_only"]})
            return
        self._post(entity_id, params)

    def set_campaign_budget(
        self,
        campaign_id: str,
        daily_amount: float,
        *,
        dry_run: bool,
        validate_only: bool,
    ) -> None:
        account = self._account_of(campaign_id, "campaign")
        self._check_account(account)
        params: dict[str, Any] = {"daily_budget": f"{daily_amount:.2f}"}
        if dry_run or validate_only:
            self._post_raw(campaign_id, {**params, "execution_options": ["validate_only"]})
            return
        self._post(campaign_id, params)

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
        self._check_account(act_id)
        params: dict[str, Any] = {
            "name": name,
            "campaign_id": campaign_id,
            # Config sets the pixel explicitly on every ad set Agon creates —
            # never rely on a connector default (examples/config.example.yaml).
            "promoted_object": json.dumps(
                {"pixel_id": pixel_id, "custom_event_type": "PURCHASE"}
            ),
            "status": status,  # born PAUSED — §5 action
        }
        if dry_run or validate_only:
            self._rehearse(f"{act_id}/adsets", params)
            return "dry-run"
        result = self._post(f"{act_id}/adsets", params)
        return str(result.get("id", ""))

    # --- framework.md §4 duplication chain -------------------------------------------

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
        """GET creative → POST creative → POST ad (PAUSED) → verify post ID.

        The post ID must survive the copy: a duplicate with a fresh post has
        zero reactions, zero comments, learning reset — everything the
        duplication exists to preserve (framework.md §4).
        """
        if not source_ad.post_id:
            raise AdapterError(
                f"source ad {source_ad.id} has no resolvable post ID — refusing "
                "to duplicate (would mint a fresh post)"
            )
        if dry_run or validate_only:
            self.create_creative_from_post(
                act_id, page_id, source_ad.post_id, source_ad.url_tags,
                dry_run=True, validate_only=True,
            )
            self.create_ad(
                act_id, dest_adset_id, "dry-run", name, status="PAUSED",
                url_tags=source_ad.url_tags, dry_run=True, validate_only=True,
            )
            return "dry-run"
        creative_id = self.create_creative_from_post(
            act_id, page_id, source_ad.post_id, source_ad.url_tags,
            dry_run=False, validate_only=False,
        )
        new_ad_id = self.create_ad(
            act_id, dest_adset_id, creative_id, name, status="PAUSED",
            url_tags=source_ad.url_tags, dry_run=False, validate_only=False,
        )
        copy = self.get_ad(new_ad_id)
        if copy.post_id != source_ad.post_id:
            raise PostIdMismatchError(
                f"duplicated ad {new_ad_id} carries post {copy.post_id!r}, "
                f"expected {source_ad.post_id!r} (framework.md §4)"
            )
        return new_ad_id


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
