# ADR-0005 — An adversarial council, not a single balanced strategist

**Status:** Accepted · **Affects:** `council.py`, `plugin/agents/`, `docs/debate-protocol.md`

## Context

The gates determine what is *eligible*. Something still has to decide what is
*wise*, because every gate encodes a worldview and every worldview has a blind
spot its own metrics cannot detect.

The obvious design is one strategist agent with a well-written, balanced prompt
holding all the mandates: grow, but prudently; test, but with data density;
protect the brand, but stay profitable.

Tested, it fails in a specific and repeatable way. Asked to weigh efficiency
against growth against risk, a single agent produces the average of those
positions, and that average suits no particular account on any particular
day. It hedges, it recommends monitoring, and it says both options have merit
while proposing a compromise nobody would have arrived at from first
principles.

A better prompt does not fix this; the failure is structural. Nothing in a
balanced prompt makes the agent pay a price for any position, so the
lowest-loss output is the one that cannot be wrong, which is also the one
that cannot be useful. Softening the personas to make them agree destroys the
only mechanism that would have surfaced the trade-off.

## Alternatives considered

**A single strategist with a sharper prompt.** Rejected: the failure is
structural, and sharpening produced confident single-lens answers instead of
hedged multi-lens ones, which is worse.

**A voting panel.** Rejected on two counts. Voting reintroduces averaging
through the back door, since a 3–2 split still resolves to a number rather than
an argument. And it would let advisory agents outnumber a mechanical safety
rule, which is exactly the property that must never hold.

**Named real practitioners as personas.** Rejected. Sharper voices, but it
means publishing fabricated opinions attributed to identifiable people. The
archetypes are composites; where a framework has a known public origin it is
credited in prose rather than ventriloquised.

## Decision

Five agents with **deliberately opposed mandates**, each with its own metrics,
its own standing arguments, and **no responsibility for the others' concerns**.
The Scaling Operator is never asked to be prudent. The Risk Officer is never
asked to be ambitious. Plus an **Adjudicator** that must rule, must name who it
ruled against, and must state what would flip the ruling.

Four design constraints make it work rather than look interesting:

**1. Only contested actions are debated.** An ad with zero carts and $200 spent
is not a matter of opinion. `council.contested()` selects only actions at least
two archetypes would score in opposite directions, roughly one in five.
Debating everything wastes tokens and, worse, trains the reader to skim.

**2. Every agent receives an identical brief.** Asymmetric briefs produce what
looks like disagreement but is really different information, which is useless
for adjudication.

**3. Two vetoes are enforced in code, not in prose.** The Risk Officer's guard
veto lives in `guards.py`; the Brand Steward's destination veto lives in
`council.py`. No argument in a round can dislodge them. Everything else is
genuinely up for argument.

**4. The Python layer never calls a model.** `council.py` prepares briefs and
enforces vetoes; the plugin runs the debate. The system's safety properties must
not depend on which model is available, or on a model behaving.

Blind spots are documented in each charter, and the agent is told to own its
own. An agent that acknowledges where its mandate misleads it and argues
anyway has done something harder than restating its priors, and the Adjudicator
is instructed to weight that.

## Consequences

- The debate is the most token-expensive part of a run. Restricting it to the
  contested subset is what makes it affordable, and `contested()` becoming too
  broad is the most likely way this decision degrades in practice.
- Transcripts are stored with the actions. Six months later the question is
  never "what did the account do" (the platform knows that) but "why did we
  think that was a good idea", and the losing argument is what makes a ruling
  auditable rather than a press release.
- Calibration is now a maintenance surface. Five openings that sound like one
  model wearing five hats mean the charters were under-loaded; the protocol
  requires passing the full charter, never a paraphrase.
- The council sits between the guards and the envelope. It can be removed
  entirely and the system still runs on gates alone, which is the fallback when
  no agent runtime is available.

## Revisit if

Contested actions routinely exceed a third of the action set: the gates are
mistuned and the council is compensating for them, which is the wrong layer.
Or if adjudications cluster on one archetype's side across many runs, which
means either that archetype is over-weighted in the brief or one of the others
is being under-briefed and should be rerun before it is believed.
