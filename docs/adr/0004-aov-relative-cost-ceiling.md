# ADR-0004 — Kill on a rolling recency window, with a cost ceiling relative to what the ad sells

**Status:** Accepted · **Affects:** `gates/kill.py` limbs C1 and C2

## Context

Two defects, discovered together in a live account.

### Defect 1 — stale conversions confer permanent immunity

The two terminal mechanisms both required a zero over a trailing 30-day
window: kill fired on zero carts or zero purchases; retirement required three
lifetime purchases before an ad was even *eligible*.

An ad with one or two lifetime purchases satisfies neither: it is not a
failure, since it has non-zero conversions, and it is not a proven winner,
since it is below the retirement floor. And because the window is thirty
days, a single conversion from three weeks ago immunises an ad that has
produced nothing for a week.

An ad with zero conversions, which performed worse, would have been killed on
day two.

Observed in one market on one day:

| 30d spend | 30d purchases | 7d spend | 7d purchases |
|---:|---:|---:|---:|
| $578 | 1 | $256 | **0** |
| $441 | 2 | $219 | **0** |
| $367 | 2 | $208 | **0** |
| $212 | 1 | $165 | **0** |

**$848 in seven days, zero purchases between them.** The worst had zero carts
over the week and a 30-day cost per cart 16.5× the market baseline. A further
~$1,500 of monthly spend sat in the same gap across the rest of the pipeline,
including one ad 36 days old with a single purchase.

### Defect 2 — nothing kills on bad unit economics

An ad converting at a ruinous cost was never terminated so long as it converted
at all.

## The flat-ceiling trap

The instinctive fix is a maximum cost per acquisition. Tested against the same
account, it destroys the best performers, because average order value ranged
from $255 to $1,140 across the catalogue:

| CPA | AOV | return | verdict |
|---:|---:|---:|---|
| **$214** | $1,043 | 4.87 | excellent |
| **$214** | $932 | 4.36 | good |
| $190 | $315 | **1.66** | **loss-making** |
| $173 | $1,140 | 6.61 | excellent |

A $150 flat ceiling kills four ads, three of them returning 4.87, 4.36 and
6.61. The one genuinely uneconomic ad has only the *third* highest cost per
acquisition. Paying $214 to win a $1,043 order is a good trade; paying $190 to
win a $315 order is not.

A flat ceiling does not control cost; it penalises selling expensive things.

## Decision

Add a third kill branch on a rolling recent window, with no lifetime
conversion condition:

```
KILL-C1 (recency):    age ≥ recent_window  AND  recent spend ≥ 150
                                           AND  recent purchases = 0

KILL-C2 (economics):  age ≥ recent_window  AND  recent spend ≥ 150
                                           AND  recent purchases ≥ 1
                                           AND  recent return < kill_floor[stage]
```

The cost ceiling is expressed relative to what the ad actually sells:

```
CPA_max = (that ad's own recent AOV) ÷ (its stage's return floor)
```

which is algebraically a return floor, and is implemented as one. The gate
evaluates the return form; the audit entry reports the implied CPA, so the
trade stays legible to a human reading the log.

Each term is deliberate. The age condition ensures the attribution window has
closed, so nothing is judged before it can convert. The spend floor is roughly
0.7× the account's AOV floor: enough delivery for the result to be signal, and
low enough that a small-spend ad never trips either branch. C1's zero is
absolute, with no baseline dependency, so baseline drift cannot erode it. The
staged floors are looser in the Proving Ground, which is discovery and needs
tolerance for learning, and tighter in Scale and Reserve, which hold proven
creative.

**Precedence: fatigue outranks kill.** An ad satisfying both retires to the
Reserve rather than dying, because retirement preserves the post ID for later
recovery and a kill does not.

## Validation

Modelled against the live pull that prompted this ADR: caught all four
gate-gap ads via C1; caught one further ad via C2 at 1.78 return against a 2.0
floor, which retirement also caught, exercising the precedence rule correctly;
zero false kills, with no ad above 4.0 return caught at any tested
threshold.

## Consequences

- Ads that convert once and stall are pruned in about a week instead of never.
  Estimated recovery in the source account: ~$850/week in one market alone.
- Kill remains terminal and irreversible. A killed ad must be relaunched as a
  new ad from the same post ID if it is later wanted.
- Raises the risk of killing a slow-cycle or seasonal creative, mitigated by
  the spend floor, the age gate, and fatigue precedence.
- Every kill-C action must report recent spend, purchases, return, AOV, the
  implied CPA and the lifetime purchase count, so the immunity that was
  bypassed is visible in the audit trail.

## Revert if

Kill-C terminates more than five ads in a single run, or a killed ad's post is
graduated from another campaign within fourteen days, evidence the creative
was viable and the branch is mistuned. In either case pause the branch and
raise the spend floor rather than lowering the return floors.
