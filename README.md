# Moot

**Adversarial multi-agent governance for paid social ad accounts.**

Five agents with deliberately opposed mandates argue every contested move. An
adjudicator rules. Target-derived gates decide what is eligible, mechanical
guards decide whether anything may execute at all, and an authorization
envelope decides what runs unattended versus what gets proposed to a human.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-483%20passing-brightgreen.svg)](tests/)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-8A63D2.svg)](plugin/)

```
┌─────────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐   ┌──────────┐
│    PULL     │──►│  GATES   │──►│ GUARDS  │──►│ COUNCIL  │──►│ ENVELOPE │──► writes
│ paginated,  │   │ what is  │   │ may we  │   │ what is  │   │ what may │
│ verified    │   │ eligible │   │ act at  │   │  wise    │   │ run un-  │
│             │   │          │   │  all    │   │          │   │ attended │
└─────────────┘   └──────────┘   └─────────┘   └──────────┘   └──────────┘
      │                                             │
   incomplete ⇒ no writes                    5 opposed agents
                                             + an adjudicator
```

## Try it in 30 seconds

No credentials, no ad account, no network. The repo ships a demo account:

```bash
pip install -e ".[dev]"
moot plan --adapter fixture --fixtures tests/fixtures --config examples/config.example.yaml
```

A slice of what prints:

```
## Baselines (docs/gates.md §3)

- AU: 12.0 (computed, n=4)
- NZ: 10.0 (computed, n=4)
- US: 12.0 (seeded, n=2, seeded from AU)

_A fallback or seeded baseline in play is how gates rot — every market states value AND source._

## Decisions

### KILL — NZ-20260810-IMG-expensive (`ad_kill_b`)
- KILL B — expensive and unconverting: age 10d ≥ 2d, trailing spend 90.0 ≥ 60,
  zero purchases, cost per cart 30.00 > 1.3 × baseline (13.00).

### FATIGUE — NZ-20260725-VID-faded (`ad_fatigue`)
- FATIGUE — proven ad decayed against its own history: cost per cart rose to
  1.3× own trailing (28.57 vs 10.00); return fell below 0.6× own trailing
  (1.50 vs 8.00).
- suppressed by precedence: **KILL** (§12; recorded so the losing decision stays visible)

### BUDGET_UP — NZ-20260806-VID-repriced (`ad_auction`)
- Auction check §7.1: creative signal stable while CPM rose 1.67× and return
  fell — auction cost shift, not creative death. Retirement converted to a
  budget/bid proposal.
- suppressed by precedence: **FATIGUE** (§12; recorded so the losing decision stays visible)
```

Then `moot debate` prints the Round 0 briefs for the contested set — on this
demo account, a demotion and both budget moves; the open-and-shut kills argue
themselves. Nothing writes without `--confirm-write`, and
[`MOOT_READ_ONLY=1`](docs/writes.md) overrides even that.

> **New to media buying?** [`docs/gates.md` §1](docs/gates.md#1-vocabulary)
> defines every term (return, cost per cart, hook rate, baseline) before it is
> used. You do not need the domain to read the design.

---

## The problem

Two failures dominate governed ad accounts, and they are opposites.

**The account nobody touches.** Proposals get generated, reviewed, agreed — and
never executed. Winners sit unharvested in a test bed. Losers keep spending. In
the account this framework was built against, an audit found weeks of correct,
unexecuted proposals: a retirement stage running dark at 5.8× return while
budget sat in stages returning under 1×.

**The account an agent touches too confidently.** One LLM with a balanced prompt
and API credentials, acting decisively on a partial data pull, is a genuinely
bad idea. A single unpaginated page looks exactly like an account that suddenly
stopped converting, and a confident system responds to that decisively.

Moot's answer to the first is to execute. Its answer to the second is that an
LLM being persuasive is never the last thing standing between a proposal and a
live ad account.

## Why adversarial, specifically

One strategist agent with a balanced prompt fails predictably: it produces the
*average* of the mandates it holds, and the average is the one answer that is
never right. It hedges. It recommends monitoring. Nothing in the prompt makes
it pay a price for any position.

So each mandate gets its own agent, its own metrics, and no responsibility for
the others. The Scaling Operator is never asked to be prudent. The Risk Officer
is never asked to be ambitious. The collision surfaces the trade-off a balanced
prompt would have quietly averaged away.

| Agent | Thesis | Blind spot |
|---|---|---|
| **Creative Architect** | Creative is the targeting | Tests forever; undervalues scale |
| **Media Economist** | Platform return is a claim, not a measurement | Starves the engine to protect the blend |
| **Scaling Operator** | Volume at target, not maximum efficiency | Mistakes a ceiling for a plateau |
| **Risk Officer** | One confident wrong run from ruin · **hard veto** | Paralysis |
| **Brand Steward** | Every impression is a deposit or withdrawal · **hard veto** | Vetoes profitable plays on taste |
| **Adjudicator** | Must rule, name the loser, state what flips it | — |

Two vetoes are enforced **in code**, not in a prompt — an agent that can be
argued out of a safety rule does not have a safety rule. Everything else is
genuinely up for argument. Charters: [`docs/agents.md`](docs/agents.md).

### The part that took longest to get right

Standing opposition is necessary but nowhere near sufficient for a debate. The
Media Economist supports *every* kill and the Creative Architect opposes *every*
kill, so opposition alone marks every kill contested — and an ad with zero carts
on $200 of spend is not a matter of opinion. Debating it wastes tokens and,
worse, trains the reader to skim the ones that matter.

So `contested()` additionally requires the **gate evidence** to show the call is
close: a graduation on return rather than efficiency, a cost per cart near the
ceiling, a budget move, a borderline auction call, a post carrying more than 40%
of its stage's revenue.

The calibration target is **roughly one action in five**. Far more means the
gates are mistuned and the council is compensating for them, which is the wrong
layer. Far fewer means the council has become decorative. It is the one number
worth watching when adapting this to another domain.

## The framework

A three-stage ladder with one scaling lever.

| Stage | Asks | Budget at |
|---|---|---|
| **Proving Ground** | Does this concept work at all? | ad set, fixed |
| **Scale** | How much can this winner absorb? | campaign — *the only place money compounds* |
| **Reserve** | Residual value in a finished winner? | campaign, return floor |

```
                 ┌── never worked ────────────────────► KILL (paused, terminal)
                 │
 PROVING GROUND ─┤              ┌── copy by post ID ──► SCALE ──► decline ──┐
                 └── proven ────┤                                            │
                                └── original stays live ──► fatigue ─────────┤
                                                                              ▼
                                                          recovery ◄────── RESERVE
```

Three rules do most of the work:

- **A graduating ad is copied, not moved.** Graduating on ad-level performance
  while scaling on ad-set performance makes the rules fight each other and
  hollows out the parent ad set.
- **Only proven creative enters the Reserve.** Zero-cart creative has no warm
  audience to recycle — it is killed, not demoted.
- **Movement is by post ID.** A new ad referencing the existing post inherits
  its social proof and ranking. A fresh ad mints a new post and resets learning.

### Every threshold derives from one number

The operator sets a return **target**. Stage floors, graduation thresholds and
budget triggers are all ratios of it, so raising the target tightens the whole
account coherently instead of leaving a scatter of hardcoded constants that
drift apart.

This is not hypothetical tidiness. In the source system the documented target
was 5.64 while the graduation, demotion and kill gates were all still written
against a 4.0 that predated it. Every document was internally consistent, and
the account was governed by three different ambitions at once
([ADR-0001](docs/adr/0001-target-as-constraint-not-filter.md)).

And the target is a **constraint, not a filter**: *maximise spend subject to the
blended return landing near target*. An ad at 3× is fine while others at 9×
carry the blend and volume grows. Scaling is judged on **marginal** return —
"does the next increment come back above target" — never on the average.

Full arithmetic, every limb, and the reasoning:
[`docs/gates.md`](docs/gates.md).

## Safety

Moot ships a **live write path**. It can pause ads, move budget, and create ads
in an account that spends real money. Ten layered mechanisms, each assuming the
ones above it have failed:

dry-run by default · `MOOT_READ_ONLY=1` overrides everything, checked inside the
dispatch function · server-side `validate_only` before every write · everything
born paused · the envelope enforced in code, with out-of-envelope actions
**downgraded to proposals rather than dropped** · account allowlist in the
adapter · budget steps clamped to +30% · **no delete verb anywhere**, enforced
by a test · post-write verification that the post ID survived · append-only
JSONL audit including dry runs.

Above all of it: **an incomplete pull means no writes at all this run.** Not a
reduced set. None.

Read [`docs/writes.md`](docs/writes.md) before unsetting the kill switch.

## Scheduling

Two Claude Code scheduled tasks against the same data day: a **daily review**
that only proposes, and an **autopilot** that executes inside the envelope.
Deliberately redundant — the review is what you keep when autonomy is switched
off, and the revert condition does exactly that: the autopilot keeps running and
reporting, and stops writing.

One trap worth stating on the front page: **a headless run cannot read this
repository.** Everything it knows is in the prompt captured when the task was
created. Editing `docs/gates.md` changes nothing until the prompt is
regenerated — so `gates_version` is stamped into every generated prompt and
`/moot:schedule status` reports drift. See
[`docs/scheduling.md`](docs/scheduling.md).

## As a Claude Code plugin

```
/plugin marketplace add <this repo>
/plugin install moot
/moot:plan account.yaml
/moot:debate account.yaml
/moot:schedule autopilot
```

## Layout

```
src/moot/          runtime — gates, baselines, pipeline, guards, adapters, writes
  gates/           one module per gate family, each citing its gates.md section
  adapters/        meta (live Graph API) · fixture (offline, what tests use)
plugin/            Claude Code plugin
  agents/          the six charters
  skills/          account-framework · adversarial-review
  commands/        /moot:plan · /moot:debate · /moot:schedule
  hooks/           pre-dispatch checks: naming, destination, plan-before-write
  scheduled-tasks/ daily review (propose-only) · autopilot (execute-in-envelope)
docs/              framework · gates · agents · debate-protocol · scheduling · writes · adr/
tests/             pytest over fixtures — no network, no credentials
```

## Scope

**What runs where.** The Python package computes gates, baselines, guards, the
contested set and the writes — and it deliberately **never calls a model**. The
five-way debate runs in the Claude Code plugin layer, which renders the briefs
this package prepares. That split is the point: the safety properties must not
depend on which model is available, or on a model behaving.

**What this is not.** Meta only — no Google Ads, no TikTok, no LinkedIn. One
account shape has been exercised end to end. It is a local operator tool, not a
multi-tenant service: no authentication of its own, and it should not be exposed
as a network service. It does not do creative production, budget forecasting, or
attribution modelling; it reads the platform's numbers, haircuts them, and says
so.

**Known limitations.** Zero-decimal currencies (JPY, KRW) use a different
minor-unit offset and are not handled. The live Meta adapter is implemented and
unit-tested against recorded payload shapes, but exercised against one real
account shape.

## Status

Early, pre-1.0, and honest about it: the [CHANGELOG](CHANGELOG.md) records what
an independent review found and what was fixed, including a currency-unit bug
that would have made budget writes 100× too small. That review is why the gate
engine, guards, pipeline and write layer are tested rather than asserted.

## Provenance

Moot generalises a production system that ran a real direct-to-consumer ad
account. The gate arithmetic, the ladder, the post-ID mechanics and the parsing
traps are all extracted from that system — every trap in
[`gates.md` §11](docs/gates.md#11-metric-parsing--the-traps) caused a wrong
decision at least once before it became a rule, and the ADRs record the ones
that cost the most.

The client's brand, account identifiers, catalogue and market structure are not
in this repository, and the agent archetypes are original composites rather than
portrayals of any named practitioner.

Default thresholds are the ratios a live account at `target = 5.64` produced.
**They are a starting point, not a recommendation.** Fit them to your own
account — [`gates.md` §2](docs/gates.md#2-target-derived-thresholds) shows where
each ratio came from so you can argue with it.

---

*moot* — from Old English *gemōt*, a deliberative assembly; the sense that
survives in "moot point" originally meant *open to argument*. The gates settle
what is not; the council argues what is.

Contributions: [`CONTRIBUTING.md`](CONTRIBUTING.md) · Security:
[`SECURITY.md`](SECURITY.md) · MIT licensed.
