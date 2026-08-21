# Agon

**Adversarial multi-agent governance for paid social accounts.**

Five agents with deliberately opposed mandates argue every contested move. An
adjudicator rules. Target-derived gates decide what is eligible, mechanical
guards decide whether anything may execute at all, and an authorization
envelope decides what runs autonomously versus what gets proposed to a human.

*agon* (ἀγών) — the formal contest at the heart of Greek drama, in which two
opposed positions argue until one prevails.

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

---

## The problem

Two failures dominate governed ad accounts, and they are opposites.

**The account nobody touches.** Proposals get generated, reviewed, agreed — and
never executed. Winners sit unharvested in a test bed. Losers keep spending. In
the account this framework was built against, an audit found weeks of correct,
unexecuted proposals: a retirement stage running dark at 5.8× while budget sat
in stages returning under 1×.

**The account an agent touches too confidently.** One LLM with a balanced prompt
and API credentials, acting decisively on a partial data pull, is a genuinely
bad idea. A single unpaginated page looks exactly like an account that suddenly
stopped converting, and a confident system responds to that decisively.

Agon's answer to the first is to execute. Its answer to the second is that an
LLM being persuasive is never the last thing standing between a proposal and a
live ad account.

## Why adversarial, specifically

One strategist agent with a balanced prompt fails predictably: it produces the
*average* of the mandates it holds, and the average is the one answer that is
never right. It hedges. It recommends monitoring. It says both options have
merit — because nothing in the prompt makes it pay a price for any position.

So each mandate gets its own agent, its own metrics, and no responsibility for
the others. The Scaling Operator is never asked to be prudent. The Risk Officer
is never asked to be ambitious. Each argues its corner as hard as it can, and
the collision surfaces the trade-off a balanced prompt would have quietly
averaged away.

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
genuinely up for argument. Full charters: [`docs/agents.md`](docs/agents.md).

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

And the target is a **constraint, not a filter**: *maximise spend subject to the
blended return landing near target*. An ad at 3× is fine while others at 9×
carry the blend and volume grows. Scaling is judged on **marginal** return —
"does the next increment come back above target" — never on the average.

Full arithmetic, including every limb and its rationale:
[`docs/gates.md`](docs/gates.md).

## Quickstart

```bash
pip install -e ".[dev]"
cp examples/config.example.yaml account.yaml   # edit: ids, markets, target
export AGON_READ_ONLY=1                        # belt

agon audit  --config account.yaml   # read-only snapshot + baselines
agon plan   --config account.yaml   # what would it do, and on what evidence
agon debate --config account.yaml   # what is contested, and why
```

Try it with no credentials at all against the bundled fixtures:

```bash
agon plan --adapter fixture --fixtures tests/fixtures --config examples/config.example.yaml
```

As a Claude Code plugin:

```
/plugin marketplace add <this repo>
/plugin install agon
/agon:plan account.yaml
/agon:debate account.yaml
/agon:schedule autopilot
```

## Safety

Agon ships a **live write path**. It can pause ads, move budget, and create ads
in an account that spends real money. Ten layered mechanisms assume the ones
above them have failed:

dry-run by default · `AGON_READ_ONLY=1` overrides everything, checked inside the
dispatch function · server-side `validate_only` before every write · everything
born paused · the envelope enforced in code, with out-of-envelope actions
**downgraded to proposals rather than dropped** · account allowlist in the
adapter · budget steps clamped to +30% · **no delete verb anywhere**, enforced
by a test · post-write verification that the post ID survived · append-only
JSONL audit including dry runs.

Above all of it: **an incomplete pull means no writes at all this run.** Not a
reduced set. None.

Read [`docs/writes.md`](docs/writes.md) before unsetting the kill switch.

## Layout

```
src/agon/          runtime — gates, baselines, pipeline, guards, adapters, writes
  gates/           one module per gate family, each citing its gates.md section
  adapters/        meta (live Graph API) · fixture (offline, what tests use)
plugin/            Claude Code plugin
  agents/          the six charters
  skills/          account-framework · adversarial-review
  commands/        /agon:plan · /agon:debate · /agon:schedule
  hooks/           pre-dispatch naming and destination checks
  scheduled-tasks/ daily review (propose-only) · autopilot (execute-in-envelope)
docs/              framework · gates · agents · debate-protocol · scheduling · writes · adr/
tests/             pytest over fixtures — no network, no credentials
```

## Scheduling

Two Claude Code scheduled tasks against the same data day: a **daily review**
that only proposes, and an **autopilot** that executes inside the envelope. They
are deliberately redundant — the review is what you keep when autonomy is
switched off, and the revert condition does exactly that: the autopilot keeps
running and reporting, and stops writing.

One trap is worth stating on the front page: **a headless run cannot read this
repository.** Everything it knows is in the prompt captured when the task was
created. Editing `docs/gates.md` changes nothing until the prompt is
regenerated — so `gates_version` is stamped into every generated prompt and
`/agon:schedule status` reports drift. See
[`docs/scheduling.md`](docs/scheduling.md).

## Provenance

Agon generalises a production system that ran a real direct-to-consumer ad
account. The gate arithmetic, the ladder, the post-ID mechanics and the parsing
traps are all extracted from that system — every trap in
[`gates.md` §11](docs/gates.md#11-metric-parsing--the-traps) caused a wrong
decision at least once before it became a rule, and the ADRs record the ones
that cost the most.

The client's brand, account identifiers, catalogue and market structure are not
in this repository, and the agent archetypes are original composites rather than
portrayals of any named practitioner.

Default thresholds are the ratios that a live account at `target = 5.64`
produced. **They are a starting point, not a recommendation.** Fit them to your
own account — [`gates.md` §2](docs/gates.md#2-target-derived-thresholds) shows
where each ratio came from so you can argue with it.

## Status

Early. The gate engine, guards, pipeline and fixture adapter are tested; the
live Meta adapter is implemented but exercised against one account shape. The
council layer prepares and enforces — it deliberately does not call a model
itself, so the safety properties do not depend on which model is available.

Contributions welcome: [`CONTRIBUTING.md`](CONTRIBUTING.md).
Security issues: [`SECURITY.md`](SECURITY.md). MIT licensed.
