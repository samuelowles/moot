---
description: Compute the day's pipeline actions and print the run report. Writes nothing.
argument-hint: [path to account config]
allowed-tools: Bash, Read, Grep, Glob
---

Compute today's pipeline plan for the account configured at
`${1:-account.yaml}` and present it.

Run:

```bash
moot plan --config ${1:-account.yaml}
```

Then read the report and add what the raw output cannot:

1. **Sanity-check the baselines first.** If any market reports
   `baseline_source: fallback` or `seeded`, say so prominently: every
   cost-relative gate in that market is running on a constant nobody has
   revisited.
2. **Flag the absences.** No graduations in 14 days means the testing engine
   has stalled and Scale starves in about three weeks. Nothing in the
   performance data says this; you have to say it.
3. **Name the contested set.** If actions are contested, say which and why, and
   offer to run `/moot:debate`. Do not adjudicate them yourself here; that is
   what the council is for.
4. **Check concentration.** If a single post carries more than 40% of stage
   revenue, raise it. The answer is more testing, not more budget.

This command **never writes**. If the plan looks right and you want to execute
it, that is `moot apply --confirm-write`, deliberately, after reading
`docs/writes.md` §5.
