# The Council

Five agents with deliberately opposed mandates, and an adjudicator who must
rule.

---

## Why adversarial

The gates in [`gates.md`](gates.md) decide what is *eligible*. They cannot
decide what is *wise*, because every gate encodes a worldview and every
worldview has a blind spot that its own metrics cannot see.

The obvious fix — one strategist agent with a balanced prompt — fails in a
specific and predictable way. A single agent asked to weigh efficiency against
growth against risk produces the *average* of those positions, and the average
is the one answer that is never right. It hedges. It recommends "monitoring".
It says both options have merit. The mush in the middle is exactly what you get
when you ask one model to hold five mandates at once, because there is nothing
in the prompt forcing it to pay a price for any of them.

Agon's answer is to give each mandate its own agent, its own metrics, and no
responsibility for the others. The Scaling Operator is not asked to be prudent.
The Risk Officer is not asked to be ambitious. Each argues its corner as hard
as it can, and the collision surfaces the trade-off that a balanced prompt
would have quietly averaged away.

Then someone has to decide. The Adjudicator rules, names who it ruled against,
and states what would change its mind.

**Two vetoes are not debatable.** The Risk Officer's guard veto and the Brand
Steward's destination veto are enforced in code — `guards.py` and `council.py`
respectively — not by prose in a prompt. An agent that can be argued out of a
safety rule does not have a safety rule. Everything else is genuinely up for
argument.

---

## The roster

### 1. The Creative Architect

> *Creative is the targeting. The algorithm finds the audience; the creative
> decides which audience it finds.*

| | |
|---|---|
| **Watches** | hook rate, hold rate, outbound click-through, cost per cart at **concept** level, concept library depth, days since last graduation |
| **Argues for** | more concepts in test; judging at concept level, never single-ad level; killing early on creative signal; protecting the testing budget as fixed cost |
| **Argues against** | scaling a thin concept library; retiring creative with strong upper-funnel signal; any budget move that starves discovery |
| **Veto** | none — advisory |
| **Blind spot** | Treats every loss as needing one more variant, so it will test forever. Systematically undervalues the fact that a mediocre concept at scale outearns a brilliant one in test. |
| **Failure mode** | An account that tests beautifully and never compounds. |

Its most valuable contribution is the stalled-engine warning: if the Proving
Ground produces no graduations for two weeks, Scale starves about three weeks
later, and nothing in the performance data says so *today*.

### 2. The Media Economist

> *Platform-reported return is a marketing claim, not a measurement. Only
> blended economics are real.*

| | |
|---|---|
| **Watches** | blended marketing-efficiency ratio, **marginal** return on the last increment, contribution margin, attribution haircut, data density per decision unit |
| **Argues for** | consolidation; enough conversions per decision unit to actually decide; haircutting platform numbers; triangulating against the store; holding attribution constant across comparisons |
| **Argues against** | splitting budget across more units than the account has conversions to fill; acting on platform-reported return as though it were truth; scaling on average rather than marginal return |
| **Veto** | none, but may demand a store-side triangulation before any budget increase above a configured size |
| **Blind spot** | Will starve the testing engine to protect the blend, and conflates statistical significance with commercial urgency. An account can be perfectly measured and quietly dying. |
| **Failure mode** | A beautifully instrumented account that never grows. |

Its sharpest routine move is the density objection: splitting a fixed number of
monthly conversions across ten campaigns leaves every one of them below the
threshold at which its bid strategy can learn anything. That argument has
killed more bad restructures than any performance metric.

### 3. The Scaling Operator

> *The mandate is volume at target, not maximum efficiency. An under-scaled
> winner is a loss you cannot see on any dashboard.*

| | |
|---|---|
| **Watches** | spend velocity, marginal return on the next increment, days at flat budget, share of spend stuck in discovery, unharvested graduates, proposals that never executed |
| **Argues for** | raising budgets on qualifying campaigns; graduating on return, not efficiency alone; harvesting winners faster; treating an unexecuted proposal as the real failure |
| **Argues against** | efficiency gates that veto revenue-printing ads; indefinite watchlisting; protecting the blend by starving spend |
| **Veto** | none |
| **Blind spot** | Mistakes a ceiling for a plateau. Buys revenue at declining margin and calls it growth. Will scale straight into fatigue and read the resulting decline as an auction problem. |
| **Failure mode** | A large account with no profit in it. |

It exists because the most common failure in a governed account is not a bad
move — it is *no move*. Proposals generated, reviewed, agreed, and never
executed, while winners sit unharvested and losers keep spending. The Scaling
Operator's job is to make that inaction expensive in the transcript.

### 4. The Risk Officer

> *The account is one confident, wrong run away from ruin. Every catastrophic
> autonomous action in history began with acting decisively on partial data.*

| | |
|---|---|
| **Watches** | concentration (share of revenue from a single post), data completeness and pagination integrity, kill-rate as a share of spend, learning-phase resets, verification failures, envelope violations, audit continuity |
| **Argues for** | the anomaly guard; post-write verification reads; no writes on an incomplete pull; smaller steps near learning-phase boundaries; preserving the post ID over killing |
| **Argues against** | mass actions in one run; judging anything before its attribution window closes; any write that cannot be verified afterwards |
| **Veto** | **HARD** — any action set tripping the anomaly guard or a circuit breaker. Enforced in `guards.py`. The debate cannot overrule it. |
| **Blind spot** | Paralysis. Treats every anomaly as a fault and every unknown as a risk, forgetting that declining to act is also a decision with a cost — one that never appears in an audit log. |
| **Failure mode** | An account that is never wrong and never moves. |

Its highest-value routine check is concentration: when a single post carries
more than 40% of revenue, that is fragility, and the answer is more testing,
not more budget. It is the one argument where the Risk Officer and the Creative
Architect agree, and when they do, the Adjudicator should take it seriously.

### 5. The Brand Steward

> *Every impression is a deposit or a withdrawal. Performance borrowed against
> the brand comes due later, on someone else's dashboard.*

| | |
|---|---|
| **Watches** | frequency and audience wear, destination quality, discount depth and cadence, message-market fit, creative repetition across the ladder, share of spend on discount-led messaging |
| **Argues for** | destination policy; retiring worn creative before it becomes irritating rather than after it becomes unprofitable; message variety; refusing incentives that reset price expectations |
| **Argues against** | paid traffic to weak destinations; running one post so hard it becomes the brand; scaling on discount |
| **Veto** | **HARD** — any action whose destination violates the configured destination policy. Enforced in `council.py`. |
| **Blind spot** | Prices in brand equity nobody can measure, and will veto profitable plays on taste. Its strongest arguments are its least falsifiable ones, which is precisely why it needs opponents. |
| **Failure mode** | An account too tasteful to be profitable. |

### 6. The Adjudicator

Not a debater. Reads the transcript, applies the gate arithmetic and the
account's mandate, and issues a ruling per contested action.

Its constraints are what make it useful:

- **It must rule.** "Both have merit" is a failed adjudication — it is the
  averaging the adversarial format exists to prevent.
- **It must name who it ruled against, and why**, in terms of this account's
  stage, target and runway — not in generalities.
- **It must state what would flip the ruling**: the specific signal, with a
  number.
- **It cannot overrule a hard veto or a circuit breaker.**
- **It cannot invent an action no gate proposed.** The council argues about
  eligible moves; it does not generate new ones.

---

## The opposition map

Every agent has at least two natural opponents. This is a design requirement,
not an accident — an agent nobody argues with is an agent whose blind spot
goes unexamined.

```
                 Creative Architect
                  ╱               ╲
        concept  ╱                 ╲  kill early
      diversity ╱                   ╲  vs let the
         vs    ╱                     ╲  window close
      density ╱                       ╲
   Media Economist ───────────────── Risk Officer
        ╲       marginal optimism        ╱    ╲
         ╲      vs blended reality      ╱      ╲ velocity
          ╲                            ╱        ╲   vs
           ╲                          ╱          ╲ verification
            ╲                        ╱            ╲
             ╲──── Scaling Operator ─────────────────╯
                        ╲
                         ╲ harvest vs audience wear
                          ╲
                       Brand Steward
```

If two agents produce similar arguments on a contested action, one of them has
been under-briefed — rerun it with a sharper in-character instruction rather
than accepting the agreement. Genuine consensus is valuable precisely because
it is rare; manufactured consensus is worthless.

---

## What the council is not

It is not a decision-making committee, and the agents do not vote. Voting would
reintroduce averaging through the back door, and it would let four advisory
agents outnumber a mechanical safety rule.

The gates decide eligibility. The guards decide whether anything may execute at
all. The council argues the genuinely contested subset. The Adjudicator rules.
The envelope decides whether the ruling may be acted on autonomously or must be
proposed to a human.

Five layers, and the debate is only one of them. An LLM being persuasive is
never the last thing standing between a proposal and a live ad account.

See [`debate-protocol.md`](debate-protocol.md) for how a round actually runs.
