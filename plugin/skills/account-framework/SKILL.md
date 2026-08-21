---
name: account-framework
description: The Agon account framework — a three-stage ladder (Proving Ground, Scale, Reserve), post-ID movement, and target-derived gates. Use when structuring or restructuring a paid-social account, deciding whether an ad should graduate, retire or be killed, diagnosing whether a decline is creative fatigue or an auction shift, or when asked how the pipeline works.
---

# The Account Framework

Reference for structuring and governing a paid-social account as a staged
ladder. Full text: `docs/framework.md`. Arithmetic: `docs/gates.md`.

## The ladder

| Stage | Asks | Budget at | Population |
|---|---|---|---|
| **Proving Ground** | Does this concept work at all? | ad set, fixed | one ad set per concept |
| **Scale** | How much can this winner absorb? | campaign | month cohorts of graduates |
| **Reserve** | Residual value in a finished winner? | campaign, return floor | retired and fatigued winners |

Transitions: **KILL** (terminal pause), **GRADUATE** (Proving→Scale),
**FATIGUE** (Proving→Reserve), **DEMOTE** (Scale→Reserve), **RECOVER**
(Reserve→Scale), **BUDGET** (Scale campaign only — the single scaling lever).

## The three rules that make it work

1. **A graduating ad is copied, not moved.** The original stays live at fixed
   budget and exits only via fatigue or kill. Graduating on ad-level
   performance while scaling on ad-set performance makes the rules fight each
   other and hollows out the parent ad set.
2. **Only proven creative enters the Reserve.** Zero-cart creative has no warm
   audience to recycle. It is killed, not demoted.
3. **Movement is by post ID.** A new ad referencing the existing post inherits
   its social proof and ranking; a fresh ad mints a new post and resets
   learning.

## Target-based movement

The mandate is **maximise spend subject to blended return landing near target**
— a constrained optimisation, not a filter. Individual ads may run below target
if the portfolio blend clears and volume is growing.

Judge scaling on **marginal** return, never average. Every threshold derives
from the single `target` value, so raising the target tightens the whole account
coherently.

## Before you act — three diagnoses

**Fatigue or auction?** Stable click-through and hold rate, risen CPM, falling
return = auction cost shift. Adjust bid or budget; do not retire. Retiring
healthy creative because the auction repriced is the most costly available
mistake.

**Decline against what?** Fatigue is measured against the ad's *own* trailing
history, not the account's. Below floor without own-history decline = watchlist.

**Weak hook, or a hook the landing page cannot cash?** High click-through with
zero carts is more damning, not less. Cart rate is an independent kill limb,
judged at concept level per market.

## Duplication pre-flight — mandatory

- **Dedupe on post ID across the whole destination campaign**, never on name.
  Names drift; a name-based check fails in both directions.
- **Derive the destination market from the source campaign**, never infer it
  from name similarity. A creative running in two markets under one name will
  otherwise collapse into one destination.
- **Carry the tracking parameters** or the copy is invisible to analytics.

## Parsing traps

Values arrive as **strings**. Action arrays are **sparse** — a missing key means
absent, not zero, so never synthesise zeros. `outbound_clicks_ctr` is a decimal
fraction. Static creative has **no hook rate** (`None`, not `0.0`) and must
never be killed on it. Paginate to exhaustion. Platform-reported revenue is for
relative ranking only — haircut it and say so.

## Commands

```bash
agon audit    # read-only snapshot + baselines
agon plan     # compute actions, print report, write nothing
agon debate   # print the contested-action briefs
agon apply --confirm-write    # dispatch inside the envelope
```
