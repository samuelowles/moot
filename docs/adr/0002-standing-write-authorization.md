# ADR-0002 — Standing write authorization inside an enumerated envelope

**Status:** Accepted · **Affects:** `writes.py`, `config.envelope`, the autopilot scheduled task

## Context

The safe default is per-change confirmation: every write described to a human,
green-lit individually, then dispatched. It is obviously correct and, past a
certain volume, it is the account's primary bottleneck.

Measured in the source system, the confirmation loop *was* the scaling
limitation. Graduation, retirement and kill proposals were produced on three
separate days and none were executed. Winners sat in a fixed-budget test bed
holding 46% of spend. A retirement stage ran dark at 5.8× return while roughly
$9,300 of monthly spend sat in entities returning at or below 1×. The analysis
was right every time, and nothing happened.

A system that identifies the correct move and cannot make it has converted a
media-buying problem into a scheduling problem, and the scheduling problem is
harder.

## Decision

Grant **standing authorization** for the scheduled autopilot to dispatch writes
without per-change confirmation, restricted to an **enumerated envelope**, with
**no cap on the number of moves per run**.

**Authorized:** ad and ad-set pause/activate; campaign pause; post-ID
duplication between stages, including creating month-cohort and retired-winners
ad sets born paused; Scale-campaign daily budget increases at or below +30% per
step; Scale and Reserve campaign budget decreases; Reserve ad-set reactivation.

**Forbidden (proposed instead):** new campaigns; targeting or audience changes;
**any ad-set budget increase in any stage**; Reserve campaign scale-up;
spend-cap changes; deletes; gate overrides; any account outside the allowlist.

Three points where this differs from the obvious design:

**No move cap.** A cap on moves per run sounds prudent and is actively harmful:
it silently drops correct actions and leaves the account in a partially-governed
state that nobody can reason about. The real risk is not *many* moves, it is
*correlated* moves from bad data, which is what the anomaly guard detects, by
share of spend rather than by count.

**Out-of-envelope actions are downgraded, not blocked.** A blocked action
disappears; a downgraded one appears in the report as a proposal with its full
gate evidence, so the operator can execute it deliberately. The downgrade
exists to preserve the reasoning.

**Ad-set budget increases are forbidden in every stage**, including the ones
where the autopilot may otherwise act. The Proving Ground is a fixed-cost test
bed whose budgets are the operator's instrument, and Scale campaign budget is
the only lever that compounds. This is the narrow rule that prevents the
graduate-and-scale contradiction described in `framework.md` §2.1.

## Consequences

- Per-change confirmation is amended, not repealed. Everything outside the
  envelope still requires it.
- Hook-based enforcement does not fire on headless runs, so naming and
  destination checks are re-implemented in-process in `writes.py`. The two
  implementations must be kept in sync, and that is a real maintenance
  liability, not a formality.
- The audit log becomes the primary governance artefact rather than a
  convenience.

## Revert conditions

Any one of: the operator disabling the task; a write landing outside the
envelope, in which case the task auto-downgrades to propose-only and flags
it; three consecutive runs with account recent-window return below the breaker
floor; or store-triangulated blended efficiency falling below target with paid
identified as the driver.

On revert the autopilot keeps running and reporting, and stops writing.
Losing visibility on top of a governance failure would make the failure harder
to diagnose.
