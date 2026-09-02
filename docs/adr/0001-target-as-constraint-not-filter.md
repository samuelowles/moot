# ADR-0001 — The return target is a constraint, and every threshold derives from it

**Status:** Accepted · **Supersedes:** nothing · **Affects:** `config.py`, every gate

## Context

Two design questions arrived together, and answering them separately produced
an incoherent account both times.

**First: what is the target *for*?** The obvious reading ("kill anything below
target") makes the target a filter. Applied to a real account it does two
harmful things at once. It kills ads that are individually below target while
the portfolio blend clears comfortably, destroying volume for no economic gain.
And it never says anything about ads *above* target, so an asset returning 8×
sits at a flat budget indefinitely while the filter congratulates it. A filter
is a floor with no ceiling and no opinion about growth, which is the wrong
shape for a mandate that is fundamentally about scale.

**Second: where do the other thresholds come from?** The system carries roughly
a dozen performance thresholds: stage kill floors, a graduation floor, budget
triggers, a breaker floor. Set independently, they were set at different times
by different reasoning, and they drifted. In the source system the documented
scaling target was 5.64 while the graduation gate, the demotion gate and the
kill floors were all still written against a 4.0 that predated it. Every
document was internally consistent and the account was governed by three
different ambitions at once.

## Decision

**The target is a constraint in an optimisation, not a filter.**

> Maximise spend subject to blended return landing near target.

Individual ads may knowingly run below target while the portfolio blend clears
and volume grows. Two things are forbidden: letting the blend rot while spend
climbs, and protecting the blend by starving the account.

One corollary deserves separate statement: **scaling decisions are
judged on marginal return, not average return.** "Is this campaign above target"
is the wrong question. "Does the next increment of budget come back above
target" is the right one. An asset at 8× flat for a fortnight is under-scaled;
an asset at 5.8× whose return fell from 11× as spend tripled is at its ceiling.
The average return distinguishes neither case.

**Every performance threshold derives from the target as a ratio.**

| Threshold | Ratio |
|---|---|
| graduation and fatigue return floor | `0.71 × T` |
| kill floor — Proving Ground, Retarget | `0.35 × T` |
| kill floor — Scale, Reserve | `0.53 × T` |
| budget scale-up trigger | `0.80 × T` |
| budget scale-down trigger | `0.53 × T` |
| circuit-breaker floor | `0.35 × T` |

The ratios are the fitted values from a live account at `T = 5.64`: the
absolute numbers that account was actually run on, expressed relative to its
target. Each is individually overridable. Overriding one is ordinary tuning;
overriding most of them means the target is wrong and should be changed instead.

Cost-per-cart multiples are deliberately not derived from the target. They
describe dispersion *within* a market (how much more expensive a marginal ad
set is than a good one), which has no relationship to the account's return
ambition. They stay as multiples of the rolling baseline.

## Consequences

- Raising the target tightens the entire account coherently in one edit. This
  is the main practical benefit, and it is large.
- Threshold drift becomes structurally impossible rather than a documentation
  discipline that decays.
- The ratios encode one account's economics. A materially different margin
  structure needs different ratios, which is why `margin.gross_margin_pct` is
  recorded and the runtime warns at config load when the derived floors are
  inconsistent with it. It warns rather than corrects: silently rewriting an
  operator's floors would be a worse failure than running slightly wrong ones.
- A filter is easier to explain to a stakeholder than a constrained
  optimisation, and "we deliberately run some ads below target" needs saying
  out loud before it is discovered in a report.

## Revisit if

Blended return holds near target while contribution margin falls, the sign
that the ratios are fitted to an economic structure that has since changed. Or
if operators routinely override three or more ratios, which means the derivation
is not carrying its weight and the ratios should be re-fitted rather than
patched.
