---
name: Feature request
about: A new capability, gate limb, or guard — argued against the framework first
title: ""
labels: enhancement
assignees: ""
---

**The problem**

What operating decision or failure mode is not covered today? Describe it in
terms of the account, not the implementation.

**Where it fits**

Which layer owns it? Moot is five layers on purpose (docs/agents.md, "What
the council is not"): the gates decide eligibility, the guards decide whether
anything may execute, the council argues the contested subset, the Adjudicator
rules, the envelope decides autonomy. A proposal that puts a judgement in the
wrong layer will be pushed back to the right one.

**For a gate change — the three edits**

CONTRIBUTING.md: a gate change is three edits, and all three are required.
Check that your proposal can supply all three:

- [ ] `docs/gates.md` — the arithmetic, with the reasoning
- [ ] The implementation, citing the section it implements
- [ ] A test that fails without the change

**Non-goals**

What this deliberately does NOT do. (Moot removes capabilities as often as it
adds them — e.g. there is no delete verb and no spend cap, by design.)

**An ADR needed?**

Is the change hard to reverse, or does it encode a judgement someone will
later want to argue with? Then it needs an ADR under `docs/adr/`.
