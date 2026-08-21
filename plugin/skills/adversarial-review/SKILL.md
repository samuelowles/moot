---
name: adversarial-review
description: Run an Agon council round — five opposed agents argue the contested moves in an ad-account plan, an adjudicator rules. Use when reviewing a plan before execution, when a scaling or retirement decision is genuinely contested, when the user asks to pressure-test a media-buying decision from opposing angles, or on "council", "debate this plan", "adversarial review".
---

# Adversarial Review

Five agents with opposed mandates argue the contested subset of a plan; an
adjudicator rules. The value is in the collision — keep the personas sharp and
genuinely opposed until adjudication.

Full protocol: `docs/debate-protocol.md`. Roster: `docs/agents.md`.

## Step 1 — Get the contested set

```bash
agon plan   --config <account.yaml> --adapter <meta|fixture>
agon debate --config <account.yaml> --adapter <meta|fixture>
```

`agon debate` prints one brief per contested action. If it prints none, there
is nothing to debate — the plan is uncontested and executes on gates alone.
Report that and stop; a manufactured debate is worse than no debate.

Never debate every action. Roughly one in five should be contested. If far more
are, the gates are mistuned and that is the finding worth reporting.

## Step 2 — Openings, in parallel

Launch all five in **one message** so they run concurrently. Keep the agent IDs
— you need them for cross-examination.

Each gets: its full charter file from `plugin/agents/`, the **identical** brief,
and this instruction:

> Argue entirely in character. Structure: (1) Position — execute/modify/defer/
> reject, first line. (2) Argument — through your metrics, with the brief's
> numbers. (3) Pre-emptive strike — name the opponent you expect and attack the
> mechanics of their position, not a strawman. 400–700 words. No hedging, no
> meta-commentary.

Pass the **full charter**, never a paraphrase. A one-line summary of a persona
produces five agents that sound identical, which is the failure mode this whole
structure exists to avoid.

Every agent gets the same facts. Asymmetric briefs produce what looks like
disagreement but is really different information — useless for adjudication.

## Step 3 — Cross-examination, in parallel

Send each agent its opponents' openings (one message, all agents). Instruction:

> (1) Attack the weakest load-bearing claims, countered with your metrics or
> their own admissions. (2) Concede with precision — vague concessions are
> worthless. (3) Revised position, changed only where their attack or your
> concession genuinely changes it. 300–500 words, same in-character rules.

Skip this round only if the openings were unanimous.

## Step 4 — Adjudicate

Hand the full transcript and gate evidence to the `adjudicator` agent. It must
produce, per action: RULING, AGAINST (who lost and why), BASIS, FLIP (a
specific signal with a number). It may not overrule a hard veto or invent an
action no gate proposed.

## Step 5 — Enforce, then report

After the ruling, the mechanical layer runs regardless:

1. **Hard vetoes** — Risk Officer on guard trips, Brand Steward on destination
   policy. Both enforced in code. A ruling of `execute` against either is
   discarded and reported.
2. **Guards** — a breaker or anomaly trip ends the run at report-only.
3. **Envelope** — an authorised action executes; anything else becomes a
   proposal with the ruling attached.

Write the ruling in full into the run report and the audit log. Openings and
cross-examinations go in summary with full text in an appendix.

## Quality bar

The round has failed if the five openings sound like one model wearing five
hats. Symptoms: everyone reaching for the same metric, no one making a precise
concession, adjectives where numbers were available, or an adjudication that
splits the difference. Rerun the weak agent with a sharper instruction rather
than accepting it — a hedged opening produces a hedged ruling three steps later.

## No Agent tool available?

Run the rounds yourself, sequentially, re-reading the relevant charter in full
before each turn and never letting one persona's reasoning leak into another's.
Slower and weaker, but the structure still beats a single balanced take.
