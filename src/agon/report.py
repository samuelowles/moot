"""Run report rendering — markdown for humans, JSON block for machines.

docs/gates.md §8: every run leads with live pipeline daily spend by stage
and the delta since the previous run. Empty sections print "None this run"
rather than being omitted, so a quiet run is distinguishable from a failed
one — a report that silently drops its watchlist is indistinguishable from a
report that never computed it.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from agon.models import Action, Decision
from agon.pipeline import RunResult
from agon.writes import DispatchResult

NONE_THIS_RUN = "None this run"


def _fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def _round_floats(value: Any) -> Any:
    """Round derived floats to 2dp for display — a threshold printed as
    4.0043999999999995 is unreadable, and the exact value lives in the audit
    JSONL where it belongs."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {k: _round_floats(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_floats(v) for v in value]
    return value


def _spend_header(result: RunResult, previous: Optional[dict[str, Any]]) -> str:
    """§8's reporting obligation — live daily spend by stage, delta since the
    previous run, at the very top of the report."""
    lines = ["## Live daily spend by stage", ""]
    prev_spend = (previous or {}).get("daily_spend") or {}
    stages = sorted(set(result.daily_spend) | set(prev_spend))
    if not stages:
        lines.append(NONE_THIS_RUN)
        return "\n".join(lines)
    lines.append("| Stage | Daily spend | Δ since last run |")
    lines.append("|---|---:|---:|")
    for stage in stages:
        current = result.daily_spend.get(stage)
        before = prev_spend.get(stage)
        if current is None and before is None:
            continue
        if before is None:
            delta = "new"
        elif current is None:
            delta = "dark"
        else:
            delta = f"{current - float(before):+,.2f}"
        lines.append(f"| {stage} | {_fmt_money(current)} | {delta} |")
    return "\n".join(lines)


def _scorecard(result: RunResult) -> str:
    """Per-stage scorecard: population, spend, return, decisions taken.

    Iterates the ENRICHED ads (``result.ads``): the raw snapshot leaves
    ``stage`` unset on the live adapter — the stage map is applied by the
    pipeline — so a raw iteration prints the whole scorecard as UNMAPPED.
    """
    lines = ["## Stage scorecard", ""]
    rows: dict[str, dict[str, Any]] = {}
    for ad in (result.ads or result.snapshot.ads):
        stage = ad.stage.value if ad.stage else "UNMAPPED"
        row = rows.setdefault(
            stage, {"ads": 0, "active": 0, "recent_spend": 0.0, "value": 0.0}
        )
        row["ads"] += 1
        if (ad.effective_status or "").upper() == "ACTIVE":
            row["active"] += 1
        if ad.recent is not None:
            row["recent_spend"] += ad.recent.spend or 0.0
            row["value"] += ad.recent.purchase_value or 0.0
    if not rows:
        lines.append(NONE_THIS_RUN)
        return "\n".join(lines)
    lines.append("| Stage | Ads | Delivering | Recent spend | Recent return |")
    lines.append("|---|---:|---:|---:|---:|")
    for stage in sorted(rows):
        row = rows[stage]
        ret = row["value"] / row["recent_spend"] if row["recent_spend"] else None
        lines.append(
            f"| {stage} | {row['ads']} | {row['active']} | "
            f"{_fmt_money(row['recent_spend'])} | {_fmt_money(ret)} |"
        )
    return "\n".join(lines)


def _action_line(action: Action) -> str:
    detail = f" — {action.rationale}" if action.rationale else ""
    return f"- `{action.verb}` → **{action.target_id}**{detail}"


def _baselines(result: RunResult) -> str:
    lines = ["## Baselines (docs/gates.md §3)", ""]
    if not result.baselines:
        lines.append(NONE_THIS_RUN)
        return "\n".join(lines)
    for market in sorted(result.baselines):
        lines.append(f"- {result.baselines[market].describe()}")
    lines.append("")
    lines.append(
        "_A fallback or seeded baseline in play is how gates rot — every "
        "market states value AND source._"
    )
    return "\n".join(lines)


def _resolutions(result: RunResult) -> str:
    lines = ["## Decisions", ""]
    decided = [
        r for r in result.resolutions if r.winner is not None
        and r.winner.decision is not Decision.NONE
    ]
    if not decided:
        lines.append(NONE_THIS_RUN)
        return "\n".join(lines)
    for resolution in decided:
        winner = resolution.winner
        if winner is None:  # decided already filters; kept defensive, no assert
            continue
        lines.append(f"### {winner.decision.value} — {resolution.ad.name} (`{resolution.ad.id}`)")
        for reason in winner.reasons:
            lines.append(f"- {reason}")
        if winner.suppressed_by is not None:
            lines.append(
                f"- suppressed by precedence: **{winner.suppressed_by.value}** "
                "(§12; recorded so the losing decision stays visible)"
            )
        evidence = json.dumps(_round_floats(winner.evidence), default=str)
        lines.append(f"  - evidence: `{evidence}`")
        if resolution.losers:
            lines.append(
                "- also fired, outranked: "
                + ", ".join(loser.decision.value for loser in resolution.losers)
            )
    return "\n".join(lines)


def render_report(
    result: RunResult,
    dispatch: Optional[DispatchResult] = None,
    previous: Optional[dict[str, Any]] = None,
) -> str:
    """The full run report as markdown."""
    parts = ["# Agon run report", ""]
    if result.guard.urgent:
        parts.append("> ⚠ **URGENT** — a guard or circuit breaker tripped; "
                     "no writes this run.")
        parts.append("")
    parts.append(_spend_header(result, previous))
    parts += ["", _scorecard(result), "", _baselines(result), "", _resolutions(result)]

    # An action downgraded to a proposal (envelope, ceiling, hard veto) is
    # reported under Proposals — an Actions line that never executes is a
    # report the operator stops trusting.
    executable = [a for a in result.actions if a.authorized]
    downgraded = [a for a in result.actions if not a.authorized]

    parts += ["", "## Actions"]
    if executable:
        parts += [_action_line(a) for a in executable]
    else:
        parts.append(NONE_THIS_RUN)

    parts += ["", "## Proposals (never executed)"]
    if result.proposals or downgraded:
        parts += [_action_line(a) for a in result.proposals + downgraded]
    else:
        parts.append(NONE_THIS_RUN)

    parts += ["", "## Watchlist"]
    if result.watchlist:
        for entry in result.watchlist:
            parts.append(f"- **{entry.entity_id}** — {entry.reasons[0] if entry.reasons else ''}")
    else:
        parts.append(NONE_THIS_RUN)

    parts += ["", "## Already dark (not judged)"]
    if result.already_dark:
        for ad in result.already_dark:
            parts.append(f"- `{ad.id}` {ad.name} — {ad.effective_status}")
    else:
        parts.append(NONE_THIS_RUN)

    parts += ["", "## Guards"]
    verdict = result.guard
    parts.append(f"- writes allowed: **{verdict.writes_allowed}**")
    if verdict.reasons:
        parts += [f"- {reason}" for reason in verdict.reasons]
    else:
        parts.append("- no guard or breaker tripped")

    if dispatch is not None:
        parts += ["", "## Dispatch"]
        counts: dict[str, int] = {}
        for outcome in dispatch.outcomes:
            counts[outcome.outcome] = counts.get(outcome.outcome, 0) + 1
        parts.append(
            "- " + (", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
                    if counts else NONE_THIS_RUN)
        )
        if dispatch.audit_path is not None:
            parts.append(f"- audit log: `{dispatch.audit_path}`")

    # Compact JSON audit block: one line of machine-readable truth.
    audit_block = {
        "daily_spend": result.daily_spend,
        "guard": {
            "writes_allowed": verdict.writes_allowed,
            "urgent": verdict.urgent,
        },
        "baselines": {
            m: {"value": b.value, "source": b.source, "population": b.population}
            for m, b in result.baselines.items()
        },
        "decisions": [
            {
                "ad_id": r.ad.id,
                "decision": r.winner.decision.value if r.winner else None,
                "suppressed_by": r.winner.suppressed_by.value
                if r.winner and r.winner.suppressed_by
                else None,
            }
            for r in result.resolutions
            if r.winner is not None
        ],
        "preflights": [
            {"status": pf.status, "source_ad_id": pf.source_ad_id} for pf in result.preflights
        ],
    }
    parts += ["", "```json", json.dumps(_round_floats(audit_block), default=str), "```", ""]
    return "\n".join(parts)


def stage_spend_line(result: RunResult) -> str:
    """One-line spend summary for CLI heads-up display."""
    if not result.daily_spend:
        return "no live spend recorded"
    return ", ".join(
        f"{stage}: {_fmt_money(value)}" for stage, value in sorted(result.daily_spend.items())
    )
