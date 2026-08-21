# Debate Protocol

How a council round runs, what gets debated, and how a ruling becomes an
action.

Roster and mandates: [`agents.md`](agents.md). Gate arithmetic:
[`gates.md`](gates.md).

---

## 1. What gets debated

Not everything. Most of a run is uncontested — an ad with zero carts and $200
spent is not a matter of opinion, and putting it to a council wastes tokens and
dilutes attention.

`council.contested()` selects actions where **at least two archetypes would
score in opposite directions**. In practice this is:

| Contested | Why |
|---|---|
| Graduations via **Path B** (return, not efficiency) | The Media Economist objects to promoting an expensive-per-cart ad; the Scaling Operator objects to vetoing a revenue-printing one. Both are right. |
| Any **SPECULATIVE** graduation | Thin evidence by construction. Risk Officer vs Scaling Operator. |
| **Budget increases** near the top of the band | Scaling Operator vs Media Economist on marginal versus average return. |
| **Retirements** where the auction check was close | Creative Architect vs Risk Officer on whether the window has closed. |
| Any action on a **concentrated** post (>40% of stage revenue) | Risk Officer and Creative Architect vs Scaling Operator. |
| Anything at the **1.80× baseline ceiling** | Explicitly propose-only per gates.md §5, so the council's job is to write the proposal's argument. |

Everything else executes on gates alone and appears in the report as
uncontested. A run with no contested actions is a normal run, not a broken one.

---

## 2. The rounds

### Round 0 — Brief

`council.brief(action)` assembles one brief per contested action. Every agent
receives **exactly the same brief**: the entity, its metrics across both
windows, the gate that fired with its evidence dict, the market baseline and
its `baseline_source`, the stage's spend and return, the account target, and
the concentration figure.

No agent gets a fact the others do not. Asymmetric briefs produce arguments
that look like disagreement but are actually different information, which is
useless for adjudication.

### Round 1 — Opening positions, in parallel

Launch the five archetypes **concurrently**, each with its charter from
[`agents.md`](agents.md) and the brief. Each returns:

1. **Position** — execute, modify, defer, or reject, stated in the first line.
2. **Argument** — through its own metrics, with numbers from the brief.
3. **Pre-emptive strike** — it knows who it is arguing against. Attack the
   position it predicts its natural opponent will take, on the mechanics, not a
   strawman.

Keep them sharp. An agent that hedges has failed its brief; rerun it rather
than accept a hedge, because a hedged opening produces a hedged ruling three
steps later.

### Round 2 — Cross-examination, in parallel

Each agent receives its opponents' Round 1 outputs and returns:

1. **Attack** — the weakest load-bearing claims in the opposing positions, each
   countered with its own metrics or the opponent's own admissions.
2. **Concede with precision** — the one or two things the opponents got right,
   stated exactly. Vague concessions are worthless; precise ones are how the
   Adjudicator locates the real disagreement.
3. **Revised position** — updated only where an attack or concession genuinely
   changes it. Hold the line everywhere else.

An agent that concedes everything is miscalibrated, and so is one that concedes
nothing. Both are worth a rerun.

Round 2 is skipped when Round 1 produced unanimity — which happens, and is a
signal in itself.

### Round 3 — Ruling

The Adjudicator receives the full transcript and the gate evidence, and for
each contested action returns:

```
RULING     execute | modify | defer | reject
AGAINST    which archetype lost, and why — in terms of this account's
           stage, target and runway
BASIS      the gate evidence and the argument that carried it
FLIP       the specific signal, with a number, that would reverse this
```

Then the mechanical layer runs regardless of what the Adjudicator said:
hard vetoes, then guards, then the envelope. A ruling of `execute` on an action
outside the envelope becomes a proposal. A ruling of `execute` on an action a
hard veto blocks is discarded and reported.

---

## 3. Where the transcript goes

Into the run report, and into the audit log alongside the action. Six months
later the question is never "what did the account do" — the platform knows
that. It is "why did we think that was a good idea", and the transcript is the
only artefact that answers it.

Store the ruling in full. Store openings and cross-examinations in summary,
with the full text in the run's appendix. A ruling without its losing argument
is not an audit trail; it is a press release.

---

## 4. Running it

In Claude Code, `/agon:debate` runs a round against the current plan. The
scheduled autopilot runs it automatically for contested actions only — see
[`scheduling.md`](scheduling.md).

Outside Claude Code, `agon debate --config … ` prints the briefs and the roster
charters so they can be fed to any agent runtime. The Python layer prepares and
enforces; it never calls a model itself. That separation is deliberate: the
safety properties of this system must not depend on which model is available,
or on a model behaving.

---

## 5. Calibration

The protocol is working when:

- The five openings sound like **five different practitioners**, not one model
  wearing five hats. Similar-sounding openings mean the charters were
  under-loaded — pass the full charter, never a one-line paraphrase.
- Both sides land **real hits** and make **precise concessions**.
- Numbers appear in every argument. An agent arguing from adjectives has
  nothing to adjudicate.
- The Adjudicator **commits**, every time, and names a loser.
- Roughly one action in five is contested. Far more means the gates are badly
  tuned; far fewer means `contested()` is too narrow and the council has become
  decorative.
