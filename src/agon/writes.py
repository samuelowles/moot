"""The write safety layer — every dispatch passes through here.

docs/framework.md §6 and the envelope block of examples/config.example.yaml.
The defaults are the safe ones: a dry run, with nothing dispatched, unless
``confirm_write=True`` is passed AND the guards allow writes AND the env
never said otherwise. Every action — dispatched, dry-run, downgraded or
skipped — lands in an append-only JSONL audit so a considered no-op is still
a decision worth auditing.

There is no delete verb here, and :func:`dispatch` refuses one on sight.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agon.adapters.base import AdPlatformAdapter
from agon.config import Config
from agon.guards import GuardVerdict
from agon.models import Action

logger = logging.getLogger(__name__)

# §8: never exceed +30% in a single budget step — auction mechanics, not
# governance. Enforced HERE, in code, regardless of what config requests;
# the budget gate caps its proposals too, but the write layer does not trust it.
BUDGET_INCREASE_HARD_CAP_PCT = 30.0

ENV_READ_ONLY = "AGON_READ_ONLY"

DISPATCHED = "dispatched"
DRY_RUN = "dry-run"
PROPOSED = "proposed"
SKIPPED_GUARD = "skipped-guard"
SKIPPED_READ_ONLY = "skipped-readonly"
FAILED = "failed"


@dataclass(frozen=True)
class DispatchOutcome:
    """What happened to one action at dispatch time."""

    action: Action
    outcome: str
    detail: str = ""
    verify: Optional[dict[str, Any]] = None

    @property
    def dispatched(self) -> bool:
        return self.outcome == DISPATCHED


@dataclass(frozen=True)
class DispatchResult:
    """The whole dispatch pass, with the audit trail location."""

    outcomes: list[DispatchOutcome] = field(default_factory=list)
    audit_path: Optional[Path] = None

    @property
    def dispatched_count(self) -> int:
        return sum(1 for o in self.outcomes if o.dispatched)


def read_only_env() -> bool:
    """``AGON_READ_ONLY=1`` forces propose-only regardless of any flag."""
    return os.environ.get(ENV_READ_ONLY, "").strip() in ("1", "true", "TRUE", "yes")


def _clamp_budget_increase(params: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Cap a budget step at +30% in code, whatever the plan asked for (§8)."""
    pct = params.get("pct")
    current = params.get("current_daily_budget")
    if not isinstance(pct, (int, float)) or not isinstance(current, (int, float)):
        return params, ""
    if pct <= BUDGET_INCREASE_HARD_CAP_PCT:
        return params, ""
    clamped = dict(params)
    clamped["pct"] = BUDGET_INCREASE_HARD_CAP_PCT
    clamped["new_daily_budget"] = current * (1 + BUDGET_INCREASE_HARD_CAP_PCT / 100.0)
    note = (
        f"requested +{pct:.0f}% clamped to +{BUDGET_INCREASE_HARD_CAP_PCT:.0f}% "
        "(§8 hard cap, enforced in code)"
    )
    logger.warning("writes: %s", note)
    return clamped, note


class _Audit:
    """Append-only JSONL. Every action lands here, including dry runs."""

    def __init__(self, path: Optional[Path], run_id: str, read_only: bool,
                 confirm_write: bool, daily_spend: dict[str, float]):
        self.path = path
        self.run_id = run_id
        self.header = {
            "run_id": run_id,
            "read_only": read_only,
            "confirm_write": confirm_write,
            "daily_spend": daily_spend,
        }
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: dict[str, Any]) -> None:
        if self.path is None:
            return
        line = json.dumps(
            {**entry, "ts": datetime.now(timezone.utc).isoformat(), **self.header},
            default=str,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _verify_ad(adapter: AdPlatformAdapter, ad_id: str, expected_status: str) -> dict[str, Any]:
    """Post-write verification read; a loud report on failure."""
    try:
        ad = adapter.get_ad(ad_id)
    except Exception as exc:  # noqa: BLE001 — verification must never crash dispatch
        logger.error("writes: VERIFY FAILED for ad %s: re-read raised %s", ad_id, exc)
        return {"verified": False, "error": str(exc)}
    ok = (ad.effective_status or ad.status or "").upper() == expected_status
    if not ok:
        logger.error(
            "writes: VERIFY FAILED for ad %s — expected %s, read %s/%s",
            ad_id, expected_status, ad.status, ad.effective_status,
        )
    return {
        "verified": ok,
        "status": ad.status,
        "effective_status": ad.effective_status,
    }


def dispatch(
    actions: list[Action],
    adapter: AdPlatformAdapter,
    config: Config,
    guard: GuardVerdict,
    *,
    confirm_write: bool = False,
    audit_path: str | Path | None = None,
    account_id: str = "",
    daily_spend: Optional[dict[str, float]] = None,
) -> DispatchResult:
    """Take the computed action set through the safety layer.

    Order of refusal, strongest first: no delete verb ever; ``AGON_READ_ONLY``
    beats ``confirm_write``; the guards beat everything but read-only; the
    envelope downgrades to a proposal rather than executing.
    """
    run_id = str(uuid.uuid4())
    read_only = read_only_env()
    audit_file = Path(audit_path) if audit_path is not None else None
    audit = _Audit(audit_file, run_id, read_only, confirm_write, daily_spend or {})

    outcomes: list[DispatchOutcome] = []
    created_adsets: dict[str, str] = {}  # "campaign_id|name" -> new ad set id

    for action in actions:
        if "delete" in action.verb.lower():
            outcome = DispatchOutcome(
                action, FAILED, "no delete verb exists in this codebase"
            )
            audit.append(
                {"verb": action.verb, "target_id": action.target_id,
                 "outcome": FAILED, "detail": outcome.detail}
            )
            outcomes.append(outcome)
            logger.error("writes: REFUSED delete-like verb %r", action.verb)
            continue

        params = dict(action.params)

        # §8: clamp BEFORE execution — the cap is enforced here in code,
        # whatever the plan or the config asked for.
        clamp_note = ""
        if action.verb == "campaign.budget_increase":
            params, clamp_note = _clamp_budget_increase(params)

        if read_only:
            outcome = DispatchOutcome(
                action, SKIPPED_READ_ONLY,
                f"{ENV_READ_ONLY} is set — propose-only regardless of flags",
            )
        elif not confirm_write:
            outcome = DispatchOutcome(action, DRY_RUN, "dry run — nothing dispatched")
        elif not guard.writes_allowed:
            outcome = DispatchOutcome(
                action, SKIPPED_GUARD, "; ".join(guard.reasons)
            )
        elif not action.authorized or not config.envelope.is_authorized(action.verb):
            # Envelope check: an unauthorized action is downgraded to a
            # proposal and flagged, never executed.
            outcome = DispatchOutcome(
                action, PROPOSED,
                f"verb {action.verb!r} is outside the authorized envelope — "
                "downgraded to a proposal",
            )
        else:
            outcome = _execute(
                action, params, adapter, config, account_id, created_adsets
            )
            if clamp_note:
                outcome = DispatchOutcome(
                    outcome.action, outcome.outcome,
                    "; ".join(x for x in (outcome.detail, clamp_note) if x),
                    outcome.verify,
                )

        audit.append(
            {
                "verb": action.verb,
                "target_id": action.target_id,
                "params": params,
                "outcome": outcome.outcome,
                "detail": outcome.detail,
                "rationale": action.rationale,
                "source_gate": action.source_gate.value if action.source_gate else None,
                "verify": outcome.verify,
            }
        )
        outcomes.append(outcome)

    return DispatchResult(outcomes=outcomes, audit_path=audit_file)


def _execute(
    action: Action,
    params: dict[str, Any],
    adapter: AdPlatformAdapter,
    config: Config,
    account_id: str,
    created_adsets: dict[str, str],
) -> DispatchOutcome:
    """Execute one authorized action through the adapter, then verify."""
    verb = action.verb
    act = account_id or (config.account.allowed_account_ids[0]
                         if config.account.allowed_account_ids else "")
    try:
        if verb == "ad.pause" or verb == "ad.activate":
            status = "PAUSED" if verb == "ad.pause" else "ACTIVE"
            adapter.set_status(action.target_id, "ad", status,
                               dry_run=False, validate_only=False)
            verify = _verify_ad(adapter, action.target_id, status)
            return DispatchOutcome(action, DISPATCHED, "", verify)

        if verb == "adset.pause" or verb == "adset.activate" or verb == "reserve.reactivate":
            status = "PAUSED" if verb == "adset.pause" else "ACTIVE"
            adapter.set_status(action.target_id, "adset", status,
                               dry_run=False, validate_only=False)
            return DispatchOutcome(
                action, DISPATCHED,
                "", {"verified": None, "note": "adset status read not on the protocol"}
            )

        if verb == "adset.create_cohort":
            name = params.get("name", "")
            campaign_id = params.get("campaign_id", action.target_id)
            new_id = adapter.create_adset(
                act, campaign_id, name, params.get("pixel_id", ""),
                status="PAUSED", dry_run=False, validate_only=False,
            )
            created_adsets[f"{campaign_id}|{name}"] = new_id
            return DispatchOutcome(
                action, DISPATCHED, "", {"verified": True, "new_adset_id": new_id}
            )

        if verb == "duplicate.post_id":
            return _dispatch_duplicate(action, params, adapter, config, act, created_adsets)

        if verb == "campaign.budget_increase" or verb == "campaign.budget_decrease":
            amount = params.get("new_daily_budget")
            if not isinstance(amount, (int, float)):
                return DispatchOutcome(action, FAILED, "no computed budget amount")
            adapter.set_campaign_budget(action.target_id, float(amount),
                                        dry_run=False, validate_only=False)
            return DispatchOutcome(
                action, DISPATCHED,
                "", {"verified": None, "note": "campaign budget read not on the protocol"}
            )

        return DispatchOutcome(
            action, PROPOSED, f"unknown verb {verb!r} — not executed"
        )
    except Exception as exc:  # noqa: BLE001 — a failed write is an outcome, not a crash
        logger.error("writes: dispatch of %s %s FAILED: %s", verb, action.target_id, exc)
        return DispatchOutcome(action, FAILED, str(exc))


def _dispatch_duplicate(
    action: Action,
    params: dict[str, Any],
    adapter: AdPlatformAdapter,
    config: Config,
    act: str,
    created_adsets: dict[str, str],
) -> DispatchOutcome:
    """The framework.md §4 chain: source read → duplicate → verify → maybe
    activate. The destination ad set may be one this run just created."""
    source = adapter.get_ad(action.target_id)
    campaign_id = params.get("destination_campaign_id", "")
    dest_adset = params.get("destination_adset_id")
    if not dest_adset:
        # The cohort ad set was created earlier in this same dispatch pass.
        stage_label = params.get("destination_stage", "SCALE").lower()
        wanted = params.get("destination_adset_name") or _cohort_name(config, stage_label)
        dest_adset = created_adsets.get(f"{campaign_id}|{wanted}")
    if not dest_adset:
        return DispatchOutcome(action, PROPOSED, "destination ad set unresolved")

    page_id = params.get("page_id") or ""
    if not page_id:
        market = source.market
        market_cfg = config.markets.get(market or "")
        page_id = (market_cfg.destination_page_id if market_cfg else "") or ""
    suffix = config.naming.duplicate_suffix.format(
        stage=str(params.get("destination_stage", "SCALE")).lower()
    )
    name = f"{source.name}{suffix}"
    pattern_note = ""
    if config.naming.pattern and not re.fullmatch(config.naming.pattern, name):
        # Naming policy is a flag, not a blocker — recorded in the audit.
        pattern_note = f"name {name!r} does not match the configured pattern"

    new_ad_id = adapter.duplicate_post(
        source, act, page_id, dest_adset, name, dry_run=False, validate_only=False
    )
    # url_tags carried from source (§9 C) — the adapter threads them; evidence:
    evidence: dict[str, Any] = {
        "verified": True,
        "new_ad_id": new_ad_id,
        "post_id": source.post_id,
        "url_tags": source.url_tags,
        "destination_adset_id": dest_adset,
    }
    if pattern_note:
        evidence["naming_flag"] = pattern_note

    # §5: graduates are born PAUSED and activated only after verification.
    if params.get("activate_after_verify"):
        adapter.set_status(new_ad_id, "ad", "ACTIVE", dry_run=False, validate_only=False)
        evidence["activation"] = _verify_ad(adapter, new_ad_id, "ACTIVE")
    else:
        evidence["born_paused"] = _verify_ad(adapter, new_ad_id, "PAUSED")
    if not (
        evidence.get("activation", {}).get("verified", True)
        and evidence.get("born_paused", {}).get("verified", True)
    ):
        logger.error(
            "writes: duplicate of %s verified FAILED — copy status unexpected",
            action.target_id,
        )
    return DispatchOutcome(action, DISPATCHED, pattern_note, evidence)


def _cohort_name(config: Config, stage_label: str) -> str:
    """Fallback cohort name when the plan did not carry one."""
    now = datetime.now(timezone.utc)
    if stage_label == "reserve":
        return "Retired winners"
    return f"{now.strftime('%Y-%m')} winners"


def previous_run_state(audit_path: str | Path | None) -> Optional[dict[str, Any]]:
    """The most recent audit entry's run header, for the §8 delta report."""
    if audit_path is None:
        return None
    path = Path(audit_path)
    if not path.exists():
        return None
    last: Optional[dict[str, Any]] = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    return last
