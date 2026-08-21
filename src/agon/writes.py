"""The write safety layer — every dispatch passes through here.

docs/framework.md §6 and the envelope block of examples/config.example.yaml.
The defaults are the safe ones: a dry run, with nothing dispatched, unless
``confirm_write=True`` is passed AND the guards allow writes AND the env
never said otherwise. Every action — dispatched, dry-run, downgraded or
skipped — lands in an append-only JSONL audit so a considered no-op is still
a decision worth auditing. The audit redacts anything token-shaped before
writing (docs/writes.md §4); a read-back that contradicts the intended
change is a FAILED_VERIFY outcome, never a success (mechanism 9).

There is no delete verb here, and :func:`dispatch` refuses one on sight —
enforced as an explicit ALLOWED-verb allowlist, so remove/destroy/archive
are refused by the same mechanism, not by substring luck.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agon.adapters.base import AdPlatformAdapter, PostIdMismatchError
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
SKIPPED_DEPENDENCY = "skipped-dependency"
FAILED = "failed"
FAILED_VERIFY = "failed-verify"

# The verbs this layer can execute, exhaustively. Anything else presented as
# executable is refused on sight (docs/writes.md mechanism 8): the absence of
# a delete verb is enforced by this allowlist, which also covers
# remove/destroy/archive — none of them are listed, so none can dispatch.
ALLOWED_VERBS = frozenset(
    {
        "ad.pause",
        "ad.activate",
        "adset.pause",
        "adset.activate",
        "adset.create_cohort",
        "campaign.pause",
        "duplicate.post_id",
        "campaign.budget_increase",
        "campaign.budget_decrease",
        "reserve.reactivate",
    }
)

# docs/writes.md §4 / SECURITY.md: the audit log redacts anything token-shaped
# before writing. Meta system-user tokens start EAA…; query-string forms are
# caught by the access_token pattern.
REDACTED = "[REDACTED]"
_TOKEN_PATTERNS = (
    re.compile(r"EAA[A-Za-z0-9]{20,}"),
    re.compile(r"access_token=\S+"),
)


def _redact(value: Any) -> Any:
    """Recursively replace token-shaped substrings in every string field."""
    if isinstance(value, str):
        for pattern in _TOKEN_PATTERNS:
            value = pattern.sub(REDACTED, value)
        return value
    if isinstance(value, dict):
        return {_redact(k): _redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


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
    """``AGON_READ_ONLY`` forces propose-only regardless of any flag.

    A kill switch must not be spelling- or case-fragile: any non-empty value
    is ON (``Yes``, ``y``, ``on``, ``TRUE``…) except the explicit OFF words
    ``0``/``false``/``no``/``off`` (case-insensitive) and the empty string.
    """
    raw = (os.environ.get(ENV_READ_ONLY) or "").strip().lower()
    if not raw:
        return False
    return raw not in ("0", "false", "no", "off")


def _numeric(value: Any) -> Optional[float]:
    """A real number, or None — bools are ints but never budgets."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _clamp_budget_increase(
    params: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    """Cap a budget step at +30% in code, whatever the plan asked for (§8).

    The recomputed ``new_daily_budget = current × (1 + min(pct, 30)/100)`` is
    ALWAYS what dispatches — a plan-supplied amount is never sent verbatim,
    because the cap is only meaningful against the real current budget. An
    action with no numeric ``current_daily_budget`` cannot be capped against
    anything and is refused outright (error return, never dispatched).
    """
    current = _numeric(params.get("current_daily_budget"))
    if current is None or current <= 0:
        return params, "", (
            "campaign.budget_increase lacks a numeric positive "
            "current_daily_budget — the §8 +30% cap cannot be verified "
            "against an unknown base, so nothing is dispatched"
        )
    pct = _numeric(params.get("pct"))
    if pct is None:
        new = _numeric(params.get("new_daily_budget"))
        if new is None:
            return params, "", (
                "campaign.budget_increase carries neither pct nor "
                "new_daily_budget — no step to cap, refused"
            )
        # Derive the implied ask from the plan's own amount, then cap it.
        pct = (new / current - 1.0) * 100.0
    effective = min(pct, BUDGET_INCREASE_HARD_CAP_PCT)
    clamped = dict(params)
    clamped["pct"] = effective
    clamped["new_daily_budget"] = current * (1.0 + effective / 100.0)
    note = ""
    if pct > BUDGET_INCREASE_HARD_CAP_PCT:
        note = (
            f"requested +{pct:.0f}% clamped to "
            f"+{BUDGET_INCREASE_HARD_CAP_PCT:.0f}% (§8 hard cap, enforced in "
            "code); new_daily_budget recomputed from current_daily_budget"
        )
        logger.warning("writes: %s", note)
    elif _numeric(params.get("new_daily_budget")) != clamped["new_daily_budget"]:
        note = (
            "new_daily_budget recomputed as current_daily_budget × "
            "(1 + pct/100) — the plan's amount is never sent verbatim (§8)"
        )
    return clamped, note, ""


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
        record = {**entry, "ts": datetime.now(timezone.utc).isoformat(), **self.header}
        line = json.dumps(_redact(record), default=str)
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


def _verify_adset(
    adapter: AdPlatformAdapter, adset_id: str, expected_status: str
) -> dict[str, Any]:
    """Ad-set status read-back (mechanism 9) — same shape as the ad verify."""
    try:
        adset = adapter.get_adset(adset_id)
    except Exception as exc:  # noqa: BLE001 — verification must never crash dispatch
        logger.error("writes: VERIFY FAILED for ad set %s: re-read raised %s", adset_id, exc)
        return {"verified": False, "error": str(exc)}
    ok = (adset.effective_status or adset.status or "").upper() == expected_status
    if not ok:
        logger.error(
            "writes: VERIFY FAILED for ad set %s — expected %s, read %s/%s",
            adset_id, expected_status, adset.status, adset.effective_status,
        )
    return {
        "verified": ok,
        "status": adset.status,
        "effective_status": adset.effective_status,
    }


def _verify_campaign_status(
    adapter: AdPlatformAdapter, campaign_id: str, expected_status: str
) -> dict[str, Any]:
    """Campaign status read-back (mechanism 9), used by campaign.pause."""
    try:
        campaign = adapter.get_campaign(campaign_id)
    except Exception as exc:  # noqa: BLE001 — verification must never crash dispatch
        logger.error(
            "writes: VERIFY FAILED for campaign %s status: re-read raised %s",
            campaign_id, exc,
        )
        return {"verified": False, "error": str(exc)}
    read = campaign.effective_status or campaign.status
    ok = read == expected_status
    if not ok:
        logger.error(
            "writes: VERIFY FAILED for campaign %s — expected %s, read %s",
            campaign_id, expected_status, read,
        )
    return {"verified": ok, "status": campaign.status,
            "effective_status": campaign.effective_status, "expected": expected_status}


def _verify_campaign_budget(
    adapter: AdPlatformAdapter, campaign_id: str, amount: float
) -> dict[str, Any]:
    """Budget read-back (mechanism 9): the campaign must now carry the amount."""
    try:
        campaign = adapter.get_campaign(campaign_id)
    except Exception as exc:  # noqa: BLE001 — verification must never crash dispatch
        logger.error(
            "writes: VERIFY FAILED for campaign %s budget: re-read raised %s",
            campaign_id, exc,
        )
        return {"verified": False, "error": str(exc)}
    read = campaign.daily_budget
    ok = read is not None and math.isclose(read, amount, rel_tol=0.005, abs_tol=0.01)
    if not ok:
        logger.error(
            "writes: VERIFY FAILED for campaign %s budget — expected %.2f, read %s",
            campaign_id, amount, read,
        )
    return {"verified": ok, "daily_budget": read, "expected": amount}


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

    Order of refusal, strongest first: only ALLOWED verbs may execute (no
    delete verb ever); ``AGON_READ_ONLY`` beats ``confirm_write``; the guards
    beat everything but read-only; the envelope downgrades to a proposal
    rather than executing. A §6/§7 pause that depends on a Reserve copy only
    dispatches after that copy verified (``requires_verified_duplicate_of``).
    """
    run_id = str(uuid.uuid4())
    read_only = read_only_env()
    audit_file = Path(audit_path) if audit_path is not None else None
    audit = _Audit(audit_file, run_id, read_only, confirm_write, daily_spend or {})

    outcomes: list[DispatchOutcome] = []
    created_adsets: dict[str, str] = {}  # "campaign_id|name" -> new ad set id
    # Source ad ids whose duplication verified ACTIVE this run (§6/§7): a
    # retirement pause dispatches only after its copy lands.
    verified_duplicates: set[str] = set()

    for action in actions:
        if action.verb not in ALLOWED_VERBS and action.authorized:
            # An action presented as executable whose verb this layer cannot
            # execute is refused on sight. Delete-like verbs are refused by
            # the same allowlist — no delete/remove/destroy/archive is listed.
            detail = (
                f"verb {action.verb!r} is not in the executable allowlist — "
                "refused. No delete/remove/destroy/archive verb exists in "
                "this codebase (docs/writes.md mechanism 8)."
            )
            if "delete" in action.verb.lower():
                detail = f"no delete verb exists in this codebase — {detail}"
            outcome = DispatchOutcome(action, FAILED, detail)
            audit.append(
                {"verb": action.verb, "target_id": action.target_id,
                 "outcome": FAILED, "detail": outcome.detail}
            )
            outcomes.append(outcome)
            logger.error("writes: REFUSED non-executable verb %r", action.verb)
            continue

        params = dict(action.params)

        # §8: clamp BEFORE execution — the cap is enforced here in code,
        # whatever the plan or the config asked for. An unverifiable base is
        # a FAILED action, never a dispatch.
        clamp_note = ""
        if action.verb == "campaign.budget_increase":
            params, clamp_note, clamp_error = _clamp_budget_increase(params)
            if clamp_error:
                outcome = DispatchOutcome(action, FAILED, clamp_error)
                audit.append(
                    {"verb": action.verb, "target_id": action.target_id,
                     "params": params, "outcome": FAILED, "detail": clamp_error}
                )
                outcomes.append(outcome)
                logger.error(
                    "writes: REFUSED budget increase on %s: %s",
                    action.target_id, clamp_error,
                )
                continue

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
            requires = params.get("requires_verified_duplicate_of")
            if requires and requires not in verified_duplicates:
                # §6/§7: the pause follows a copy that verifies. No verified
                # copy this run → the source keeps delivering.
                outcome = DispatchOutcome(
                    action, SKIPPED_DEPENDENCY,
                    f"source ad {requires} has no verified copy this run — "
                    "the §6/§7 pause only follows a duplication that "
                    "verified (gates.md §6/§7)",
                )
            else:
                try:
                    outcome = _execute(
                        action, params, adapter, config, account_id, created_adsets
                    )
                except PostIdMismatchError as exc:
                    # Mechanism 9: a duplication whose post ID did not
                    # survive raises loudly — never accepted as success.
                    audit.append(
                        {"verb": action.verb, "target_id": action.target_id,
                         "params": params, "outcome": FAILED_VERIFY,
                         "detail": f"post ID mismatch: {exc}"}
                    )
                    outcomes.append(
                        DispatchOutcome(action, FAILED_VERIFY, str(exc))
                    )
                    raise
                if (
                    action.verb == "duplicate.post_id"
                    and outcome.outcome == DISPATCHED
                ):
                    # The copy verified — retirements of this source may now
                    # pause it (§6/§7).
                    verified_duplicates.add(action.target_id)
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
    """Execute one authorized action through the adapter, then verify.

    Every status/budget write is followed by a read-back (mechanism 9): a
    read-back that contradicts the intended change is FAILED_VERIFY, never a
    success. PostIdMismatchError is re-raised — it propagates loudly.
    """
    verb = action.verb
    act = account_id or (config.account.allowed_account_ids[0]
                         if config.account.allowed_account_ids else "")
    try:
        if verb == "ad.pause" or verb == "ad.activate":
            status = "PAUSED" if verb == "ad.pause" else "ACTIVE"
            adapter.set_status(action.target_id, "ad", status,
                               dry_run=False, validate_only=False)
            verify = _verify_ad(adapter, action.target_id, status)
            if not verify.get("verified"):
                return DispatchOutcome(
                    action, FAILED_VERIFY,
                    f"read-back shows ad {action.target_id} not {status}: "
                    f"{verify.get('effective_status') or verify.get('status')}",
                    verify,
                )
            return DispatchOutcome(action, DISPATCHED, "", verify)

        if verb == "adset.pause" or verb == "adset.activate" or verb == "reserve.reactivate":
            status = "PAUSED" if verb == "adset.pause" else "ACTIVE"
            adapter.set_status(action.target_id, "adset", status,
                               dry_run=False, validate_only=False)
            verify = _verify_adset(adapter, action.target_id, status)
            if not verify.get("verified"):
                return DispatchOutcome(
                    action, FAILED_VERIFY,
                    f"read-back shows ad set {action.target_id} not {status}: "
                    f"{verify.get('effective_status') or verify.get('status')}",
                    verify,
                )
            return DispatchOutcome(action, DISPATCHED, "", verify)

        if verb == "campaign.pause":
            # gates.md §8 permits pausing a Scale or Reserve campaign as the
            # alternative to a scale-down. There is deliberately no
            # campaign.activate counterpart: bringing a campaign back up is a
            # decision about strategy, not about performance, and stays with
            # the operator.
            adapter.set_status(action.target_id, "campaign", "PAUSED",
                               dry_run=False, validate_only=False)
            verify = _verify_campaign_status(adapter, action.target_id, "PAUSED")
            if not verify.get("verified"):
                return DispatchOutcome(
                    action, FAILED_VERIFY,
                    f"read-back shows campaign {action.target_id} not PAUSED: "
                    f"{verify.get('effective_status') or verify.get('status')}",
                    verify,
                )
            return DispatchOutcome(action, DISPATCHED, "", verify)

        if verb == "adset.create_cohort":
            name = params.get("name", "")
            campaign_id = params.get("campaign_id", action.target_id)
            new_id = adapter.create_adset(
                act, campaign_id, name, params.get("pixel_id", ""),
                status="PAUSED", dry_run=False, validate_only=False,
            )
            created_adsets[f"{campaign_id}|{name}"] = new_id
            verify = _verify_adset(adapter, new_id, "PAUSED")
            verify["new_adset_id"] = new_id
            if not verify.get("verified"):
                return DispatchOutcome(
                    action, FAILED_VERIFY,
                    f"created ad set {new_id} did not read back PAUSED: "
                    f"{verify.get('effective_status') or verify.get('status')}",
                    verify,
                )
            return DispatchOutcome(action, DISPATCHED, "", verify)

        if verb == "duplicate.post_id":
            return _dispatch_duplicate(action, params, adapter, config, act, created_adsets)

        if verb == "campaign.budget_increase" or verb == "campaign.budget_decrease":
            amount = params.get("new_daily_budget")
            if not isinstance(amount, (int, float)):
                return DispatchOutcome(action, FAILED, "no computed budget amount")
            adapter.set_campaign_budget(action.target_id, float(amount),
                                        dry_run=False, validate_only=False)
            verify = _verify_campaign_budget(adapter, action.target_id, float(amount))
            if not verify.get("verified"):
                return DispatchOutcome(
                    action, FAILED_VERIFY,
                    f"read-back shows campaign {action.target_id} carrying "
                    f"{verify.get('daily_budget')}, expected {float(amount):.2f}",
                    verify,
                )
            return DispatchOutcome(action, DISPATCHED, "", verify)

        return DispatchOutcome(
            action, FAILED, f"unknown verb {verb!r} — not executed"
        )
    except PostIdMismatchError:
        # Mechanism 9: raise loudly rather than accept a post-ID loss.
        raise
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
    if not page_id:
        # §9 B spirit: without the page the creative cannot reference the
        # post — propose, never mint a fresh one.
        return DispatchOutcome(
            action, PROPOSED,
            f"destination page id unresolved for market {source.market!r} — "
            "duplicating without it would mint a fresh post",
        )
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
        "new_ad_id": new_ad_id,
        "post_id": source.post_id,
        "url_tags": source.url_tags,
        "destination_adset_id": dest_adset,
    }
    if pattern_note:
        evidence["naming_flag"] = pattern_note

    # §5: duplicates are born PAUSED and activated only after verification —
    # including Reserve copies (§6/§7: the source pause follows a copy that
    # verifies ACTIVE, so the copy must actually reach ACTIVE).
    if params.get("activate_after_verify"):
        adapter.set_status(new_ad_id, "ad", "ACTIVE", dry_run=False, validate_only=False)
        evidence["activation"] = _verify_ad(adapter, new_ad_id, "ACTIVE")
        evidence["verified"] = bool(evidence["activation"].get("verified"))
    else:
        evidence["born_paused"] = _verify_ad(adapter, new_ad_id, "PAUSED")
        evidence["verified"] = bool(evidence["born_paused"].get("verified"))
    if not evidence["verified"]:
        logger.error(
            "writes: duplicate of %s FAILED verification — copy status unexpected",
            action.target_id,
        )
        return DispatchOutcome(
            action, FAILED_VERIFY,
            f"copy {new_ad_id} did not verify — read-back contradicts the "
            "intended status (mechanism 9)",
            evidence,
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
