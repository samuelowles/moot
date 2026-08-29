# Gate Reference

The complete decision arithmetic. This document is the specification the
runtime implements; `src/agon/gates/` is its executable form and
`tests/test_gates_*.py` its proof. If the two disagree, that is a bug in one of
them — say which in the issue.

Conceptual background is in [`framework.md`](framework.md).

---

## 1. Vocabulary

| Term | Definition |
|---|---|
| `return` | Attributed conversion value ÷ spend, for a stated window and attribution setting. Called ROAS by the platforms. |
| `cost_per_cart` | Spend ÷ add-to-carts. Called CPATC. The primary Proving Ground efficiency metric — it reads far earlier than cost per purchase. |
| `cost_per_acquisition` | Spend ÷ purchases. Reported, rarely gated on directly (see §6.2). |
| `aov` | Attributed conversion value ÷ purchases, for the ad, for the window. |
| `hook_rate` | 3-second views ÷ impressions. **Undefined for static creative** — never a kill signal there. |
| `hold_rate` | Thruplays ÷ 3-second views. |
| `cart_rate` | Add-to-carts ÷ outbound clicks. Healthy band is account-specific; recompute per run (§3.2). |
| `baseline` | Rolling top-quartile cost-per-cart for a market (§3). |
| `T` | The account's return **target**. The single performance constant an operator sets. |
| `age` | Days since the ad first delivered. |

**Windows.** `recent` defaults to 7 days, `trailing` to 30. Both are configurable.
All gates state which they use. `recent` must be at least as long as the
attribution window, or the gate judges conversions that have not had time to
land.

---

## 2. Target-derived thresholds

Every performance threshold is a ratio of `T`. Defaults below were fitted
against a live account operating at `T = 5.64`; the fitted column shows the
absolute value that produced, which is where each ratio comes from.

| Threshold | Ratio of `T` | At `T = 5.64` | Used by |
|---|---|---|---|
| `graduate_return_floor` | `0.71 × T` | 4.00 | §5 Path B |
| `fatigue_return_floor` | `0.71 × T` | 4.00 | §6, §7 |
| `kill_floor.proving` | `0.35 × T` | 2.00 | §4 C2 |
| `kill_floor.retarget` | `0.35 × T` | 2.00 | §4 C2 |
| `kill_floor.scale` | `0.53 × T` | 3.00 | §4 C2 |
| `kill_floor.reserve` | `0.53 × T` | 3.00 | §4 C2 |
| `budget_up_trigger` | `0.80 × T` | 4.50 | §8 |
| `budget_down_trigger` | `0.53 × T` | 3.00 | §8 |
| `breaker_floor` | `0.35 × T` | 2.00 | §10 |

Every ratio is overridable per account. Overriding one is ordinary tuning;
overriding most of them means the target is wrong — fix the target instead.

> **Margin assumption.** The Proving Ground and Retarget floor of `0.35 × T`
> assumes roughly 65% gross margin and a 30% haircut on platform-reported
> return. Below that margin these floors are too low and must rise.
> `config.margin.gross_margin_pct` records the account's actual figure and the
> runtime **warns at load** when the configured floors are inconsistent with
> it. It does not silently correct them.

Baseline-relative multiples are **not** target-derived — they describe
dispersion within a market, not the account's return ambition:

| Multiple | Default | Used by |
|---|---|---|
| `graduate_efficiency_max` | `0.85 × baseline` | §5 Path A |
| `graduate_return_cpc_ceiling` | `1.80 × baseline` | §5 Path B |
| `kill_cost_max` | `1.30 × baseline` | §4 B |
| `fatigue_cost_ratio` | `1.30 ×` own trailing | §6 |
| `fatigue_return_ratio` | `0.60 ×` own trailing | §6 |

---

## 3. Baseline computation

Recomputed every run, per market, before any gate evaluates.

```
candidates = ad sets where
    market  == this market
  & stage   in {PROVING, SCALE}          # not Reserve — its economics differ
  & spend(trailing) >= baseline_min_spend
  & carts(trailing) > 0

if len(candidates) < baseline_min_population:      # default 4
    baseline = config.markets[market].baseline_fallback
    baseline_source = "fallback"
else:
    ranked   = sort(candidates, by=return(trailing), desc=True)
    quartile = ranked[: max(1, ceil(len(ranked) / 4))]
    baseline = mean(cost_per_cart(trailing) for a in quartile)
    baseline_source = "computed"
```

Rules:

- **Top quartile only.** Including losers inflates the bar until the gate stops
  discriminating.
- **Per market, always.** Never evaluate a market against another market's
  baseline.
- **A new market has no baseline.** Until it reaches
  `baseline_min_population`, seed from the nearest configured analogue market
  and run **kill gates only** — no graduations. A market cannot promote on a
  bar it has not yet earned.
- The run report states both value and `baseline_source` for every market. A
  fallback baseline silently in play is how gates rot.

### 3.2 Cart-rate band

`cart_rate_band` is recomputed the same way — the min and max cart rate across
the top-quartile ad sets — and used by §4 D. Never trust a hardcoded band; it
is account- and catalogue-specific.

---

## 4. KILL — terminal pause

Applies at ad level, any stage. **Terminal**: a killed ad is not demoted, not
recycled, not reversed by any other gate. Zero-cart creative has no warm
audience worth preserving.

An ad is killed if **any** limb fires. All limbs require `effective_status`
to be genuinely delivering — an ad whose parent ad set is paused matches many
limbs but pausing it is a no-op. Filter those out and report them as
already-dark.

### A — Never worked (trailing window)

```
age >= 2d  AND  spend(trailing) >= kill_a_min_spend      # default 30
           AND  carts(trailing) == 0
```

### B — Expensive and unconverting (trailing window)

```
age >= 2d  AND  spend(trailing) >= kill_b_min_spend      # default 60
           AND  purchases(trailing) == 0
           AND  cost_per_cart(trailing) > 1.30 × baseline
```

### C1 — Recency: stalled (recent window)

```
age >= recent_window  AND  spend(recent) >= kill_c_min_spend    # default 150
                      AND  purchases(recent) == 0
```

### C2 — Recency: uneconomic (recent window)

```
age >= recent_window  AND  spend(recent) >= kill_c_min_spend
                      AND  purchases(recent) >= 1
                      AND  return(recent) < kill_floor[stage]
```

C1 and C2 exist because A and B both require a **zero over a trailing window**,
which hands permanent immunity to an ad with one or two stale conversions: not
a failure, not a proven winner, and structurally unkillable. Perversely, an ad
that performed *worse* — zero conversions — dies on day two. C1 and C2 carry no
lifetime-conversion condition, which is the entire point. See
[ADR-0004](adr/0004-aov-relative-cost-ceiling.md).

C2 is the AOV-relative ceiling from [`framework.md` §3.2](framework.md#32-cost-ceilings-are-relative-to-what-the-ad-sells),
in its algebraic form. `CPA_max = aov ÷ kill_floor[stage]` and
`return < kill_floor` are the same statement; the runtime evaluates the return
form and **reports the implied CPA** so the trade is legible in the audit.

### D — Dead click quality (concept level, per market)

```
aggregate over all ads carrying concept C in market M, recent window:
    outbound_clicks >= 100  AND  carts == 0
 OR outbound_clicks >= 100  AND  cart_rate < cart_rate_floor    # default 1.5%
```

Judged at **concept** level because the same creative runs across many ad sets
and no single ad's click count is thick enough to read. Judged **per market**
because the same asset is routinely alive in one and dead in another. When it
fires, every ad carrying that concept in that market is paused.

This limb is independent of hook rate and click-through by design — it catches
the opposite failure mode. High click-through with zero carts is the more
damning signal, not the mitigating one.

### Starved ads

```
age >= 4d  AND  spend(lifetime) < starved_max_spend      # default 5
```
→ pause. Not a performance judgement: delivery has concentrated elsewhere and
the ad is occupying a slot without producing signal.

### Precedence

**FATIGUE outranks KILL.** An ad satisfying both retires to the Reserve rather
than dying, because retirement preserves the post ID for later recovery and a
kill does not. Every other gate loses to KILL.

---

## 5. GRADUATE — Proving Ground → Scale

By post ID. Both paths require the volume gates.

```
Volume (both paths):
    spend(trailing) >= graduate_min_spend            # default 200
    purchases(trailing) >= graduate_min_purchases    # default 3
    outbound_ctr >= graduate_min_ctr                 # default 0.01
    hook_rate >= graduate_min_hook                   # default 0.20, VIDEO ONLY

Path A — efficiency:
    cost_per_cart(trailing) <= 0.85 × baseline

Path B — return:
    return(recent) >= 0.71 × T   OR   return(trailing) >= 0.71 × T
    AND cost_per_cart(trailing) <= 1.80 × baseline

Speculative (optional, config.graduate.speculative_enabled):
    passes volume gates except purchases >= 2
    AND return(trailing) >= speculative_return_min      # default 8.0
    → graduate, flagged SPECULATIVE
```

Notes:

- **`outbound_ctr` arrives as a decimal fraction.** `0.0114` is 1.14%. The 1%
  floor is `0.01`. This has burned every implementation that assumed percent.
- **Hook rate is skipped entirely for static creative**, not defaulted to zero.
  Judging statics on hook rate wipes out the best performers in most accounts.
- **Path B exists because a CPATC-only gate vetoes ads that are demonstrably
  printing revenue.** In one observed run it produced zero graduations while
  four ads sat between 5.86× and 9.95×. Its `1.80 × baseline` ceiling sits
  *above* the `1.30 ×` kill threshold — deliberately. An ad can qualify on
  return while being expensive per cart. That is a real, defensible trade, but
  anything past `1.80 ×` is **proposed, not executed**.

**Action.** Run the §9 pre-flight, then duplicate by post ID into the
destination market's current month cohort ad set (`YYYY-MM winners`), creating
that ad set if absent, born `PAUSED`, then activate after verification. **Do
not pause the source** — see [`framework.md` §2.1](framework.md#21-three-rules-that-make-the-ladder-work).

---

## 6. FATIGUE — Proving Ground → Reserve

A **proven** ad that decayed. All five conditions:

```
1. purchases(lifetime) >= fatigue_min_lifetime_purchases     # default 3
2. age >= fatigue_min_age                                    # default 7d
3. spend(recent) >= fatigue_min_spend                        # default 150
4. return(recent) < 0.71 × T
5. Decline against the ad's OWN history — at least one of:
       cost_per_cart(recent) >= 1.30 × cost_per_cart(trailing)
       return(recent)        <  0.60 × return(trailing)
```

Condition 1 enforces "only proven creative enters the Reserve". Condition 5 is
what separates fatigue from a bad week: an ad failing 1–4 with no decline
against its own baseline goes on the **watchlist**, not to the Reserve.

**Gated by the auction check (§7).**

**Action.** §9 pre-flight → duplicate by post ID into the market's Reserve
retired-winners ad set → **pause the source after the copy verifies ACTIVE**.
If the pre-flight skips the duplication because the post is already in the
destination, still pause the source: the retirement is complete, only the copy
was redundant.

---

## 7. DEMOTE — Scale → Reserve

```
spend(recent) >= demote_min_spend                # default 150
AND return(recent) < 0.71 × T
AND purchases(lifetime) >= demote_min_lifetime_purchases    # default 3
AND age >= demote_min_age                        # default 7d
```

The age condition is not decoration. An ad judged before its attribution window
closes reads far worse than it is — one observed retarget ad read 0.30× on day
three and settled at 8.46×.

**Action.** As §6, then pause the Scale copy after verification.

### 7.1 The auction check — gates every retirement

Before any FATIGUE or DEMOTE action:

```
if   outbound_ctr(recent) >= 0.90 × outbound_ctr(trailing)
and  (hook_rate undefined OR hook_rate(recent) >= 0.90 × hook_rate(trailing))
and  cpm(recent) > 1.30 × cpm(trailing)
and  return(recent) < return(trailing):
        → NOT fatigue. Auction cost shift.
        → Emit a BUDGET_OR_BID proposal. Do not retire.
```

Stable creative signal, risen CPM, falling return: the auction got more
expensive, the creative did not die. Retiring a healthy asset because the
auction repriced is the most costly mistake available to this system, which is
why it is a hard gate and not a heuristic.

---

## 8. BUDGET — the only scaling lever

Applies to **Scale-stage campaign budgets only**.

```
Scale up:
    campaign return(recent) >= 0.80 × T
    AND campaign spend(recent) >= budget_up_min_spend        # default 100
    → increase by budget_step_pct                            # default +25%, hard cap +30%

Scale down:
    campaign return(recent) < 0.53 × T
    AND campaign spend(recent) >= budget_down_min_spend      # default 200
    → decrease by budget_down_pct (default −30%) or pause

Reserve reactivation:
    Reserve campaign ACTIVE but its ad set paused
    AND campaign return(trailing) >= 0.80 × T
    → reactivate the ad set
```

> **Not implemented:** the reactivation trigger was originally specified to
> include an ad set *delivering under 50% of its budget over 3 days*, as a
> second signal alongside an outright pause. That limb needs a 3-day delivery
> window the runtime does not currently pull — every other gate reads the
> `recent` and `trailing` windows only — so it is documented here and absent
> from the code rather than half-built. Reactivation fires on the paused-ad-set
> condition alone.
>
> Adding it means adding a third window to the pull, which is a real cost for a
> gate that fires rarely. If you implement it, note that a Reserve ad set
> under-delivering while *not* paused is usually a bid-floor symptom rather than
> a status problem, so the correct action may be a bid adjustment rather than a
> reactivation.

Hard rules:

- **Never exceed +30% in a single step.** This is auction mechanics — a larger
  step resets the learning phase — not governance. Compounding daily steps are
  fine.
- **All ad-set budgets are frozen to autonomous increase**, in every stage. A
  qualifying ad set is *reported*, never raised. Proving Ground budgets belong
  to the operator.
- **Reserve campaign scale-up is proposed, not executed**, by default.
- **Cutting a loser is not scaling.** Decreases and pauses stay authorised
  whatever the scale-up policy.
- Every run reports live pipeline daily spend by stage with the delta since the
  previous run, at the top of the report. This obligation replaces a spend cap.

---

## 9. Duplication pre-flight

Mandatory before every duplication in §5, §6 and §7. Failing either check means
**do not create the ad**.

### A — Idempotency, on post ID

1. Resolve the source's post ID (`creative{effective_object_story_id}`).
2. Enumerate **every** ad in the destination *campaign* — all ad sets, all
   statuses, paginated to exhaustion.
3. If that post ID appears on any non-`ARCHIVED` ad there → **skip**, report
   `already-present` with the existing ad ID and status.
4. If the existing copy is `PAUSED` → **still skip**. A paused copy usually
   means a prior demotion or a deliberate operator pause; re-creating it
   silently overrides a decision someone already made. Surface it as a
   proposal.

Never dedupe on name. See [`framework.md` §4](framework.md#4-post-id-is-the-unit-of-the-pipeline).

### B — Market routing, derived not inferred

The destination market **must equal** the source ad's market, resolved by
looking the source's `campaign_id` up in the configured stage map. Never select
by name similarity or by defaulting. If the source campaign is unmapped, or the
market has no destination campaign for the target stage, do not duplicate —
report it as a proposal.

### C — Carry the tracking parameters

Carry `url_tags` from source to copy. A duplicate that loses its UTM taxonomy
is invisible to every downstream analytics surface.

---

## 10. Guards

Evaluated after the action set is computed, before anything dispatches.

### Anomaly guard

```
if paused_spend_share > anomaly_guard_pct of trailing-recent pipeline spend:   # default 50%
        execute NOTHING. Report URGENT.
```

Not a cap on the number of moves — there is none. A mass-kill signal indicates
bad data far more often than bad ads: a broken pull, an attribution lag, a
partial page.

### Circuit breakers — no writes, urgent report

1. Account `return(recent) < 0.35 × T`.
2. The data pull failed, was partial, or paginated inconsistently.
3. The anomaly guard tripped.

**An incomplete pull means no writes this run.** Report-only, flag the gap.
This is the single most important operational rule in the system: every
catastrophic autonomous action starts with acting confidently on partial data.

---

## 11. Metric parsing — the traps

Every one of these has produced a wrong decision in production.

1. **All platform metric values arrive as strings.** Cast before arithmetic.
2. **Action arrays are sparse, keyed by `action_type`.** A missing `purchase`
   entry means *absent from this response*, not zero. Never synthesise zeros;
   propagate absence and let the gate decide.
3. **Purchases** are `omni_purchase`, falling back to `purchase`. **Carts** are
   `omni_add_to_cart` → `add_to_cart`. Value comes from the matching key in the
   action-values array.
4. **`outbound_clicks_ctr` is a decimal fraction**, not a percentage.
5. **Static creative has no video actions.** Hook rate is `None`, not `0.0`,
   and every gate consuming it must skip rather than fail the ad.
6. **Pagination hides data.** Follow cursors to exhaustion. Analysing page one
   and presenting it as complete is the classic silent failure.
7. **The window end is inclusive.** Never combine an explicit range with a
   named preset.
8. **Hold attribution windows constant across every comparison** in a run.
9. **Platform-reported revenue is for relative ranking only.** It
   over-attributes its own conversions and misses the halo it drives elsewhere.
   Rank ads against each other with it; judge absolute account health against
   the store. Apply the configured haircut (default 30%) whenever reporting
   platform return as though it were true return, and say that you have.

---

## 12. Precedence, in full

```
1. Circuit breaker      → no writes at all
2. Anomaly guard        → no writes at all
3. Auction check        → converts FATIGUE/DEMOTE into a budget proposal
4. FATIGUE              → beats KILL (preserves the post ID)
5. KILL                 → beats everything below
6. DEMOTE
7. GRADUATE
8. BUDGET
```

Exactly one transition per ad per run. The pipeline resolves collisions by this
order and records the losing gate in the audit entry, so a suppressed decision
stays visible.
