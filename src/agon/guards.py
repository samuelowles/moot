"""Guards — docs/gates.md §10.

Evaluated after the action set is computed, before anything dispatches. The
anomaly guard is not a cap on the number of moves — there is none. A mass-kill
signal indicates bad data far more often than bad ads: a broken pull, an
attribution lag, a partial page. An incomplete pull means no writes this run:
"every catastrophic autonomous action starts with acting confidently on
partial data."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from agon.config import Config
from agon.models import Action

# §10: unreported recent spend above this share of the KNOWN pipeline spend is
# a data-quality trip — the anomaly guard is least likely to fire exactly when
# the data is partial, so partial data itself must trip.
SPEND_UNKNOWN_SHARE_LIMIT = 0.20


@dataclass(frozen=True)
class RunSnapshot:
    """The run-level facts the guards judge.

    ``pull_complete`` is False on any failed, partial or inconsistently
    paginated read (§10 breaker 2). ``pipeline_recent_spend`` is the recent
    spend of the delivering pipeline; ``paused_recent_spend`` is the recent
    spend of the entities the computed action set would pause. Ads whose
    recent spend is UNREPORTED are excluded from both of those figures and
    carried as ``spend_unknown`` (an estimated currency figure) and
    ``spend_unknown_ads`` (a count) — unreported spend must make the guards
    MORE suspicious, never read as a smaller paused share (§10).
    """

    pull_complete: bool = True
    pull_errors: tuple[str, ...] = ()
    account_recent_return: Optional[float] = None
    pipeline_recent_spend: float = 0.0
    paused_recent_spend: float = 0.0
    spend_unknown: float = 0.0
    spend_unknown_ads: int = 0

    def to_dict(self) -> dict[str, Any]:
        """The snapshot as evidence - lands in the guard verdict's audit."""
        return {
            "pull_complete": self.pull_complete,
            "pull_errors": list(self.pull_errors),
            "account_recent_return": self.account_recent_return,
            "pipeline_recent_spend": self.pipeline_recent_spend,
            "paused_recent_spend": self.paused_recent_spend,
            "spend_unknown": self.spend_unknown,
            "spend_unknown_ads": self.spend_unknown_ads,
        }


@dataclass(frozen=True)
class GuardVerdict:
    """The guards' ruling on the computed action set."""

    writes_allowed: bool
    reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    urgent: bool = False


def _breaker_trips(
    snapshot: RunSnapshot, config: Config, evidence: dict[str, Any]
) -> list[str]:
    """Breakers 1 and 2 (§10): account return below the breaker floor, and
    any incomplete pull. Each reason here forbids writes for the whole run."""
    reasons: list[str] = []
    breaker_floor = config.threshold("breaker_floor")
    evidence["breaker_floor"] = breaker_floor
    account_return = snapshot.account_recent_return
    if account_return is not None and account_return < breaker_floor:
        reasons.append(
            f"CIRCUIT BREAKER 1: account recent return {account_return:.2f} < "
            f"breaker floor {breaker_floor:.2f} (§10)."
        )
    elif account_return is None:
        reasons.append(
            "CIRCUIT BREAKER (data): account recent return unreported — an "
            "unverifiable account-level read cannot authorize writes (§10)."
        )
    if not snapshot.pull_complete:
        detail = f" ({'; '.join(snapshot.pull_errors)})" if snapshot.pull_errors else ""
        reasons.append(
            f"CIRCUIT BREAKER 2: data pull incomplete{detail}. No writes this "
            "run; report-only, flag the gap (§10)."
        )
    return reasons


def _anomaly_trips(
    snapshot: RunSnapshot, config: Config, evidence: dict[str, Any]
) -> list[str]:
    """The anomaly guard and the unknown-spend data-quality trip (§10).

    Denominator: the delivering pipeline's KNOWN recent spend — unreported
    spend is never ``or 0``-ed in, which would make a mass pause read
    artificially small exactly when the data is partial. Trip is >, not >=,
    and only with real spend.
    """
    reasons: list[str] = []
    guard_pct = config.guards.anomaly_guard_pct
    share = (
        snapshot.paused_recent_spend / snapshot.pipeline_recent_spend
        if snapshot.pipeline_recent_spend > 0
        else 0.0
    )
    evidence["paused_spend_share"] = share
    evidence["anomaly_guard_pct"] = guard_pct
    evidence["spend_unknown"] = snapshot.spend_unknown
    evidence["spend_unknown_ads"] = snapshot.spend_unknown_ads
    if share > guard_pct / 100.0:
        reasons.append(
            f"ANOMALY GUARD: this action set would pause {share:.0%} of "
            f"pipeline recent spend (> {guard_pct:.0f}%). A mass-kill signal "
            "indicates bad data more often than bad ads — executing nothing, "
            "reporting URGENT (§10)."
        )
    # Unreported spend is itself a partial-data signal: when the estimated
    # unknown recent spend exceeds 20% of the KNOWN pipeline spend, the
    # footprint this run acts on is largely unverifiable — the same class of
    # gap as breaker 2, so no writes this run.
    if (
        snapshot.pipeline_recent_spend > 0
        and snapshot.spend_unknown > SPEND_UNKNOWN_SHARE_LIMIT * snapshot.pipeline_recent_spend
    ):
        reasons.append(
            f"CIRCUIT BREAKER (data): {snapshot.spend_unknown_ads} delivering "
            f"ad(s) report no recent spend — an estimated "
            f"{snapshot.spend_unknown:.2f} exceeds 20% of the known pipeline "
            f"spend {snapshot.pipeline_recent_spend:.2f}. Partial spend data "
            "cannot authorize writes (§10)."
        )
    return reasons


def evaluate_guards(
    _actions: list[Action], snapshot: RunSnapshot, config: Config
) -> GuardVerdict:
    """Run the anomaly guard and the three circuit breakers (§10).

    The action set is accepted but deliberately unread — the guard judges the
    *data the actions were computed from*, not the actions. Any trip means
    ``writes_allowed=False`` for the whole run, including the actions that
    look individually sound.
    """
    evidence: dict[str, Any] = {"snapshot": snapshot.to_dict()}
    reasons = _breaker_trips(snapshot, config, evidence)
    reasons += _anomaly_trips(snapshot, config, evidence)
    if reasons:
        # Every trip above is urgent: no writes happened and the report must
        # say why at the top.
        return GuardVerdict(
            writes_allowed=False, reasons=reasons, evidence=evidence, urgent=True
        )
    return GuardVerdict(writes_allowed=True, reasons=[], evidence=evidence)


# Backwards-friendly alias used by the pipeline and CLI.
evaluate = evaluate_guards
