# The Framework

Moot governs a paid-social account as a **three-stage ladder** with a **single
scaling lever** and a **target-derived gate set**. This document defines the
model. [`gates.md`](gates.md) defines the arithmetic.

---

## 1. The problem this shape solves

A flat ad account cannot answer the only question that matters: *which concepts
win, which scale, and which are finished?* Every ad competes with every other
ad for the same budget under the same optimiser, so a winner and a corpse look
identical at the campaign level, and spend drifts toward whatever converted
most recently rather than whatever converts most durably.

Splitting the account into stages makes the question answerable, because each
stage asks exactly one thing:

| Stage | Asks | Budget lives at | Population |
|---|---|---|---|
| **Proving Ground** | *Does this concept work at all?* | ad set (fixed) | one ad set per concept |
| **Scale** | *How much can this winner absorb?* | campaign | month cohorts of graduates |
| **Reserve** | *Is there residual value in a finished winner?* | campaign, return floor | retired and fatigued winners |

An optional **Retarget** stage is supported and governed, but its economics are
structurally different and it is never used as a scaling lever.

---

## 2. The ladder

```
                    ┌── never worked ─────────────────────► KILL (paused, terminal)
                    │
  PROVING GROUND ───┤
  fixed budget      │                ┌── copy by post ID ──► SCALE ──► decline ──┐
  discovery         └── proven ──────┤                       compounds           │
                                     └── original stays live ──► fatigue ────────┤
                                                                                  ▼
                                                                              RESERVE
                                                                          recovers margin
                                                                                  │
                                                                     recovery ────┘
                                                                          back to SCALE
```

Six transitions, each with a gate in [`gates.md`](gates.md):

- **KILL** — terminal pause. The creative never produced a cart, or its unit
  economics are ruinous. Terminal because there is nothing to preserve.
- **GRADUATE** — Proving Ground → Scale, by post ID. The concept cleared its
  gates.
- **FATIGUE** — Proving Ground → Reserve, by post ID. A proven ad decayed
  against **its own history**.
- **DEMOTE** — Scale → Reserve, by post ID. A scaled ad decayed.
- **RECOVER** — Reserve → Scale. Retired proof came back to life.
- **BUDGET** — the only scaling lever. Scale-stage campaign budget only.

### 2.1 Three rules that make the ladder work

**The graduating ad does not leave the Proving Ground.** It is *copied* up, not
moved. The original keeps harvesting its own audience at fixed budget; the copy
compounds against campaign budget. It exits only via fatigue or kill.

The failure this prevents is specific and expensive. If you graduate on
*ad-level* performance but scale on *ad-set-level* performance in the same
pass, the rules fight each other: the ad set earns a budget increase on the
strength of one ad, then that ad is moved out, leaving the raised budget behind
whatever is left. Freezing Proving Ground budgets and copying rather than
moving removes the contradiction. The Proving Ground is a fixed-cost test bed;
Scale is the only place money compounds.

**Only proven creative enters the Reserve.** The Reserve recycles fatigued
*winners* against cheap warm inventory. Creative that never produced a cart has
no warm audience to recycle and no earning history to recover — sending it to
the Reserve just relocates the burn. It is killed instead.

**Movement is by post ID, never by recreation.** See §4.

---

## 3. Target-based movement

> **Maximise spend subject to blended return landing near target.**

This is the mandate, and it is a **constrained optimisation, not a filter**. It
does not mean "kill everything below target". It means: grow total spend, and
let the *portfolio blend* land near the target. An individual ad may knowingly
run at 3× while others at 9× carry the blend, provided total revenue is
growing. What is forbidden is letting the blend rot while spend climbs, or
protecting the blend by starving the account.

Two corollaries do most of the work in practice:

**Judge scaling on marginal return, not average return.** The question is never
"is this campaign above target?" It is "does the *next* increment of budget come
back above target?" An asset at 8× that has been flat at the same budget for a
fortnight is under-scaled. An asset at 5.8× whose return has fallen from 11× as
spend tripled is at its ceiling. Same account, opposite prescriptions, and the
average return tells you neither.

**Every gate derives from one number.** The target is the only performance
constant an operator sets. Stage floors, graduation thresholds and budget
triggers are all expressed as ratios of it, so raising the target tightens the
whole account coherently instead of leaving a scatter of hardcoded numbers that
drift apart. The default ratios are in [`gates.md` §2](gates.md#2-target-derived-thresholds);
the reasoning for deriving rather than hardcoding is in
[ADR-0001](adr/0001-target-as-constraint-not-filter.md).

### 3.1 Baselines move; constants do not

Cost-per-cart gates are expressed as multiples of a **rolling baseline**, not
as currency amounts. The baseline is the mean cost-per-cart of the
**top-quartile ad sets by return** within a market, over a trailing window,
above a minimum spend.

Top quartile, not all ad sets — including losers inflates the bar until the
gate stops discriminating. Per market, because the same creative routinely runs
4–9% click-to-cart in one market and under 1% in another. Recomputed every run,
because an account that is scaling is an account whose costs are moving.

### 3.2 Cost ceilings are relative to what the ad sells

A flat cost-per-acquisition ceiling destroys good accounts whenever average
order value varies across the catalogue. Worked from live data on a catalogue
spanning $255–$1,140 order values, a flat ceiling killed three ads returning
4.87×, 4.36× and 6.61× while leaving the genuinely uneconomic ad — the one with
only the *third* highest cost per acquisition — untouched. Paying $214 to win a
$1,043 order is a good trade; paying $190 to win a $315 order is not.

So the ceiling is relative to the ad's own realised order value:

```
CPA_max = (that ad's own trailing AOV) ÷ (its stage's return floor)
```

which is algebraically a return floor, and is implemented as one. See
[ADR-0004](adr/0004-aov-relative-cost-ceiling.md).

---

## 4. Post ID is the unit of the pipeline

Meta has no native "move ad" operation, and creating a fresh ad mints a **new
post** — zero reactions, zero comments, learning reset. Every published ad is
backed by a page post (`effective_object_story_id`, shaped `{page_id}_{post_id}`).
A new ad that *references that existing post* inherits the accumulated social
proof and engagement ranking.

Every promotion, retirement and market clone in Moot is therefore: read the
source ad's post ID → create a creative from that post ID in the destination →
create an ad from that creative, paused → verify the post ID survived → pause
the source if the transition requires it.

Two consequences that are easy to get wrong, both learned the expensive way:

**Deduplicate on post ID, never on name.** Names drift — suffixes get appended,
sources get renamed, and two genuinely different posts can share a name. A
name-based idempotency check fails in *both* directions: it reports distinct
posts as duplicates and re-creates true duplicates. In one observed run this
produced three copies of the same post in one campaign while blocking a
legitimate promotion. The post ID is the unit of the pipeline, so it is the
only valid dedupe key.

**Derive the destination market from the source, never infer it.** Look the
source ad's campaign up in the configured stage map and use *that* market's
destination. Never select by name similarity or by scanning for an existing
similarly-named copy. A creative running in two markets under one name will
otherwise collapse into a single destination, retiring one market's ad while
its twin keeps spending unnoticed.

Both checks are mandatory pre-flight on every duplication, and a skipped
duplication is reported — a no-op that was *considered* is still a decision
worth auditing.

---

## 5. Diagnosis before action

Three checks sit in front of the gates and override them. Each exists because
the naive reading of the same data produces a confidently wrong move.

**Fatigue or the auction?** Falling return with *stable* click-through and hold
rate, alongside a materially risen CPM, is an auction cost shift — not creative
death. The correct response is a bid or budget adjustment; retiring the
creative destroys a working asset and replaces it with a search for one. This
is the single most costly available mistake, so it gates every retirement.

**Decline against what?** Fatigue is measured against **the ad's own history**,
not against the account. An ad below the floor that has not declined relative
to its own trailing baseline is having a bad week, not a terminal one. It goes
on the watchlist, not to the Reserve.

**Weak hook, or a hook writing cheques the landing page cannot cash?** Hook and
click-through gates detect only one failure mode. The opposite failure — strong
click-through, clicks that never reach a cart — is more damning, not less: the
creative is buying the wrong visitor, and every additional dollar buys more of
them. Cart rate is therefore an independent kill limb, judged at *concept*
level across a market rather than per ad, because a single ad's click count is
usually too thin to read.

---

## 6. What the framework refuses to do

Constraints are as much a part of the framework as the gates. Moot has no
delete verb anywhere in its codebase — retirement is always a pause, because
the entity ID and its lifetime metrics are the audit anchor. It never edits the
creative on a duplicated post, because edits reset learning and can detach the
post; new hooks go to fresh Proving Ground tests. It never moves creative
between markets implicitly. And it will not scale an ad set — the Proving
Ground's budgets belong to the operator, and Scale campaign budget is the only
lever it may pull.

The safety model behind that last point — envelopes, dry runs, breakers and the
audit trail — is in [`writes.md`](writes.md).

---

## 7. Where the agents come in

The gates decide what is *eligible*. They cannot decide what is *wise*, because
every gate encodes a worldview and every worldview has a blind spot. A pure
efficiency lens starves the testing engine; a pure creative lens spends forever
on discovery; a pure growth lens buys revenue at declining margin.

Moot's answer is not a better single strategist. It is five agents with
deliberately opposed mandates who argue the contested moves, and an adjudicator
who must rule. See [`agents.md`](agents.md) and
[`debate-protocol.md`](debate-protocol.md).
