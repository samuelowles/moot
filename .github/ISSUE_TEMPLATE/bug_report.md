---
name: Bug report
about: Something the runtime did that docs/ say it should not (or vice versa)
title: ""
labels: bug
assignees: ""
---

**What happened**

A short description of the wrong behaviour. If it is a decision you disagree
with, name the gate and the entity — the audit entry's `evidence` dict is the
starting point for every one of these.

**What the docs say should happen**

Cite the section: `docs/gates.md` §4 B, `docs/debate-protocol.md` §2, …
If the code and the doc disagree, say which one you think is wrong —
`docs/gates.md` preamble: "If the two disagree, that is a bug in one of them."

**The numbers**

Paste the entity's metrics for both windows, the baseline in play and its
`baseline_source`, and the account target. Redact account ids and tokens —
never paste a bearer token; the audit log is redacted for a reason.

**To reproduce**

Command line, config excerpts (thresholds only — no credentials), and whether
it was a dry run or `--confirm-write`.

**Version**

Commit hash and Python version.
