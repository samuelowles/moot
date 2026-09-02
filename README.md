# Moot

**Adversarial multi-agent governance for paid social ad accounts.**

Five agents with opposed mandates argue every contested move, and an
adjudicator rules. Mechanical gates decide which ads are eligible to move,
guards decide whether the run may write at all, and an authorization envelope
separates what runs unattended from what gets proposed to a human.

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

## Quick start

Runs offline against bundled fixtures. No credentials or ad account needed:

```bash
pip install -e ".[dev]"
moot plan --adapter fixture --fixtures tests/fixtures --config examples/config.example.yaml
```

Part of the output:

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

`moot debate` then prints briefs for the contested actions. On the demo account
that is a demotion and two budget moves; the clear-cut kills are settled by the
gates and skip the council. Nothing writes without `--confirm-write`, and
[`MOOT_READ_ONLY=1`](docs/writes.md) forces read-only mode regardless of flags.

If you don't work in media buying: [`docs/gates.md` §1](docs/gates.md#1-vocabulary)
defines every term (return, cost per cart, hook rate, baseline) before it is
used.

## The problem

Governed ad accounts fail in two ways.

Some are never touched. Proposals get written, reviewed, agreed, and never
executed. In the account this system was built on, an audit found weeks of
approved, unexecuted proposals: a retirement stage running at 5.8× return
while budget sat in stages returning under 1×.

Others are touched too confidently. A single LLM with API credentials will act
decisively on a partial data pull, and one unpaginated page of results looks
identical to an account that suddenly stopped converting.

Moot executes, and it keeps language models out of the final check between a
proposal and a live account.

## Why five opposed agents

A single strategist agent holding every mandate averages them and recommends
monitoring. Splitting the mandates into separate agents makes the trade-offs
visible: the Scaling Operator is never asked to be prudent, and the Risk
Officer is never asked to be ambitious.

| Agent | Position | Blind spot |
|---|---|---|
| **Creative Architect** | Creative is the targeting | Tests forever; undervalues scale |
| **Media Economist** | Distrusts platform-reported return | Underfunds testing to protect the blend |
| **Scaling Operator** | Maximum volume at target return | Mistakes a ceiling for a plateau |
| **Risk Officer** | Limits the blast radius of any run (hard veto) | Too cautious |
| **Brand Steward** | Guards brand and destination quality (hard veto) | Vetoes profitable plays on taste |
| **Adjudicator** | Must rule, and must state what would flip the ruling | — |

The two vetoes are enforced in code, so no prompt can argue past them.
Everything else is open to argument. Charters: [`docs/agents.md`](docs/agents.md).

Opposition alone would send every kill to the council (the Media Economist
supports every kill, the Creative Architect opposes every kill), so
`contested()` also requires the gate evidence to be close: a graduation on
return rather than efficiency, a cost per cart near its ceiling, a budget
move, a borderline auction call, or a post carrying more than 40% of its
stage's revenue. The calibration target is about one action in five reaching
the council. Much more than that means the gates need tuning; much less means
the council is doing nothing.

## The framework

A three-stage ladder.

| Stage | Asks | Budget at |
|---|---|---|
| **Proving Ground** | Does this concept work at all? | ad set, fixed |
| **Scale** | How much budget can this winner absorb? | campaign |
| **Reserve** | Is there residual value in a finished winner? | campaign, with a return floor |

```
                 ┌── never worked ────────────────────► KILL (paused, terminal)
                 │
 PROVING GROUND ─┤              ┌── copy by post ID ──► SCALE ──► decline ──┐
                 └── proven ────┤                                            │
                                └── original stays live ──► fatigue ─────────┤
                                                                              ▼
                                                          recovery ◄────── RESERVE
```

Three rules carry most of the design:

- A graduating ad is copied to Scale and the original stays live in the
  Proving Ground. Moving it would drain the ad set it graduated from.
- Only proven creative enters the Reserve. Creative that never converted has
  no warm audience to recycle and is paused outright.
- Ads move by post ID. A duplicate that references the original post keeps
  its comments, shares and ranking signals; a fresh ad starts learning from
  zero.

### Thresholds derive from one target

The operator sets a return target. Stage floors, graduation thresholds and
budget triggers are all ratios of that number, so changing the target moves
every gate together. The source system had drifted exactly here: its
documented target was 5.64 while the graduation, demotion and kill gates were
still written against an earlier 4.0
([ADR-0001](docs/adr/0001-target-as-constraint-not-filter.md)).

The target constrains the blend rather than filtering single ads: the goal is
maximum spend with blended return near target, so a 3× ad is acceptable while
9× ads carry the blend. Scaling decisions use marginal return, whether the
next increment comes back above target, never the account average.

Full arithmetic for every gate: [`docs/gates.md`](docs/gates.md).

## Safety

Moot has a live write path. It can pause ads, move budget, and create ads in
an account spending real money. Ten mechanisms sit in front of that, each
assuming the ones before it have failed:

1. Dry-run by default; dispatch requires `--confirm-write`.
2. `MOOT_READ_ONLY=1` forces read-only mode regardless of flags, checked
   inside the dispatch function.
3. A server-side `validate_only` call precedes every write.
4. Every created ad and ad set is born paused.
5. The authorization envelope is enforced in code; out-of-envelope actions
   become proposals instead of being dropped.
6. The adapter refuses writes to any account not on its allowlist.
7. Budget steps are clamped to +30%.
8. No delete verb exists in the adapter protocol, and a test enforces that.
9. Post-write verification confirms a duplicate kept its post ID.
10. An append-only JSONL audit log records everything, dry runs included.

An incomplete data pull disables all writes for that run. Read
[`docs/writes.md`](docs/writes.md) before unsetting the kill switch.

## Scheduling

Two Claude Code scheduled tasks run against the same data day: a daily review
that only proposes, and an autopilot that executes inside the envelope. The
overlap is the rollback plan: switch autonomy off and the autopilot stops
writing while the review keeps reporting.

A headless run cannot read this repository. It knows only the prompt captured
when the task was created, so editing `docs/gates.md` changes nothing until
the prompt is regenerated. To catch that, `gates_version` is stamped into
every generated prompt and `/moot:schedule status` reports drift. Details:
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
src/moot/          runtime: gates, baselines, pipeline, guards, adapters, writes
  gates/           one module per gate family, each citing its gates.md section
  adapters/        meta (live Graph API) and fixture (offline, what tests use)
plugin/            Claude Code plugin
  agents/          the six charters
  skills/          account-framework, adversarial-review
  commands/        /moot:plan, /moot:debate, /moot:schedule
  hooks/           pre-dispatch checks: naming, destination, plan-before-write
  scheduled-tasks/ daily review (propose-only), autopilot (execute-in-envelope)
docs/              framework, gates, agents, debate-protocol, scheduling, writes, adr/
tests/             pytest over fixtures; no network, no credentials
```

## Scope

The Python package computes gates, baselines, guards, the contested set and
the writes. It never calls a model. The debate itself runs in the Claude Code
plugin layer, which renders briefs the package prepares, so the safety
properties hold no matter which model is available or how it behaves.

Meta only: no Google Ads, TikTok or LinkedIn. It is a local operator tool
with no authentication of its own and should not be exposed as a network
service. It does no creative production, budget forecasting or attribution
modelling; it reads the platform's numbers and applies a documented haircut.

Known limitations: zero-decimal currencies (JPY, KRW) use a different
minor-unit offset and are not handled. The live Meta adapter is unit-tested
against recorded payload shapes but has run against one real account shape.

## Status

Pre-1.0. The [CHANGELOG](CHANGELOG.md) records what two independent review
passes found and fixed, including a currency-unit bug that would have made
budget writes 100× too small.

## Provenance

Moot generalises a production system that ran a real direct-to-consumer ad
account. The gate arithmetic, the stage ladder, the post-ID mechanics and the
parsing traps in [`gates.md` §11](docs/gates.md#11-metric-parsing--the-traps)
all come from that system; each parsing trap caused at least one wrong
decision before it became a rule. The client's brand, account identifiers,
catalogue and market structure are not in this repository, and the agent
archetypes are composites that do not portray any named practitioner.

Default thresholds are the ratios a live account at `target = 5.64` produced.
Fit them to your own account before trusting them;
[`gates.md` §2](docs/gates.md#2-target-derived-thresholds) shows where each
ratio came from.

---

Contributions: [`CONTRIBUTING.md`](CONTRIBUTING.md) · Security:
[`SECURITY.md`](SECURITY.md) · MIT licensed.
