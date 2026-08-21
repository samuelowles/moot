---
description: Run an adversarial council round on the contested actions in the current plan.
argument-hint: [path to account config]
allowed-tools: Bash, Read, Grep, Glob, Task
---

Run a full council round on the contested actions for the account at
`${1:-account.yaml}`.

Load the `adversarial-review` skill and follow it exactly. In short:

```bash
agon debate --config ${1:-account.yaml}
```

If that prints no contested actions, say so and stop. A manufactured debate is
worse than no debate — report that the plan is uncontested and executes on
gates alone.

Otherwise:

1. Launch all five archetypes **in a single message** so they run concurrently.
   Pass each its **full charter file** from `plugin/agents/` — never a
   paraphrase — plus the identical brief. Keep the agent IDs.
2. Cross-examine: send each agent its opponents' openings, all in one message.
3. Hand the full transcript plus gate evidence to the `adjudicator`.
4. Report the rulings. Each must name who lost, why, and what would flip it.

Then state plainly which rulings survive the mechanical layer: hard vetoes
(Risk Officer on guard trips, Brand Steward on destination policy), then the
guards, then the envelope. A ruling of `execute` on an action outside the
envelope is a proposal for the operator, not a decision.
