# Scheduling

Agon runs unattended as two **Claude Code scheduled tasks** against the same
data day: one that only proposes, one that executes inside an envelope.

---

## 1. The two tasks

| | **Daily Review** | **Pipeline Autopilot** |
|---|---|---|
| Fires | early, before the operator's day | ~2h later |
| Authority | **propose only** — no ad-account writes, ever | executes inside the envelope; everything else proposed |
| Council | full round on contested actions | contested actions only |
| Output | decision-ready review to the report sink | actions taken + proposals + JSON audit block |
| Purpose | a human reads the account with coffee | close the proposal-to-execution loop |

They are deliberately redundant. The Daily Review is what you keep when you
turn autonomy off — and the revert condition in
[ADR-0002](adr/0002-standing-write-authorization.md) does exactly that: the
autopilot keeps running and reporting, and stops writing.

**If both ever write, the autopilot wins.** The Daily Review never writes, so
this should be unreachable; it is stated because "unreachable" states are the
ones that eventually happen.

---

## 2. Creating them

In Claude Code, with the plugin installed:

```
/agon:schedule review      # sets up the propose-only task
/agon:schedule autopilot   # sets up the execute-in-envelope task
```

Each generates a scheduled task whose prompt is built from
`plugin/scheduled-tasks/*.md` with the account's config values interpolated.

Verify with `/agon:schedule status`, which lists both tasks, their cron
expressions, the local time each currently fires, and — importantly — the gate
version each prompt was generated from.

---

## 3. The prompt is the runtime authority

This is the single most important operational fact about scheduled runs, and it
is a trap that has cost real money.

**A headless run cannot read your workspace.** It has no access to the repo,
the config file on disk, or the docs. Everything it knows is in the prompt text
captured when the task was created.

Two consequences:

1. **The task prompt is a snapshot, not a reference.** Editing `docs/gates.md`
   changes nothing about what the scheduled task does. The prompt must be
   regenerated.
2. **A gate change is not shipped until the prompt is regenerated.** An ADR that
   revises a threshold has changed the documentation and nothing else. Run
   `/agon:schedule regenerate` and confirm the version stamp moved.

The plugin defends this as far as it can: every generated prompt carries a
`gates_version` stamp, `/agon:schedule status` compares it against the repo's
current gate version, and a mismatch is reported as drift. It cannot fix drift
for you, because regenerating a prompt is a decision about what the account
should do, not a formatting operation.

Where a runtime *can* reach the repo, prefer calling the CLI (`agon plan`,
`agon apply --confirm-write`) over re-deriving the gates in prose. The prompt
then carries only the run contract, and the arithmetic stays in one tested
place.

---

## 4. The run contract

Both tasks follow the same sequence. Deviating from it is how autonomous
systems cause damage.

```
1. PULL      insights at ad, ad-set and campaign level, both windows,
             attribution held constant, paginated to exhaustion.
2. VERIFY    the pull is complete.
             ── INCOMPLETE OR PARTIAL ⇒ NO WRITES THIS RUN. Report the gap. ──
3. BASELINE  recompute per market. Record baseline_source.
4. GATE      evaluate; resolve to one transition per ad by precedence.
5. GUARD     anomaly guard + circuit breakers. A trip ends the run at report-only.
6. COUNCIL   debate the contested subset; adjudicate.
7. ENVELOPE  authorized ⇒ execute. Everything else ⇒ propose.
8. EXECUTE   validate_only pre-flight → dispatch → verification read.
9. AUDIT     append every action, skip and proposal to the JSONL log.
10. REPORT   spend by stage and delta first, then everything else.
             Empty sections print "None this run".
```

Step 2 is not optional and not a formality. **Every catastrophic autonomous
action starts with acting confidently on partial data.** A single unpaginated
page looks exactly like a healthy account that suddenly stopped converting, and
the gates will respond to it decisively.

---

## 5. Cron, timezones, and the drift that will bite you

Scheduled tasks fire on **fixed UTC cron expressions**. Daylight saving is not
handled for you.

A task set to fire at 07:00 local during standard time will fire at 08:00 local
once the clock shifts, and stay there until you retune it. This is easy to miss
because the task keeps succeeding — it just runs at the wrong hour, which
matters when the contract assumes a full previous day of data.

Practical handling:

- Record the **intended local time** in the task description, not just the
  cron. `/agon:schedule status` prints intended-vs-actual local time and flags
  the gap.
- Set a calendar reminder at each DST boundary for the account's timezone,
  covering both the shift and the return.
- Prefer a fire time at least two hours after local midnight so a one-hour
  drift in either direction cannot cross a day boundary. Drifting from 07:00 to
  08:00 is an inconvenience; drifting from 00:30 to 23:30 the previous day
  silently changes which data day the run analyses.

---

## 6. Notification sends are not idempotent

The report sink — Slack, email, a webhook — is usually a non-idempotent write.
A blind retry after an uncertain send produces a duplicate report, and
duplicate reports train operators to stop reading reports.

If a send's outcome is unknown, **record it as unknown and do not retry**. The
audit log is the durable artefact; the notification is a convenience. Losing a
notification costs an operator one lookup. Duplicating them costs their
attention permanently.

---

## 7. Failure modes worth alerting on

Not all of these are performance problems. Some are the absence of an event,
which no performance dashboard will ever show you.

| Signal | Meaning |
|---|---|
| Run did not fire | Scheduler or credential failure. The account is ungoverned and nothing is complaining. |
| Writes blocked ≥3 consecutive runs | Persistent data or auth problem masquerading as caution. |
| No graduations for 14 days | The testing engine has stalled. Scale starves in about three weeks. **Nothing in today's performance data says this.** |
| `baseline_source: fallback` persisting | Population never recovered; gates are running on a constant nobody has revisited. |
| Verification failure after a write | A dispatch may have half-landed. Stop and inspect before the next run. |
| `gates_version` drift | The prompt no longer matches the documented gates. |

---

## 8. Revert

Autonomy is granted by [ADR-0002](adr/0002-standing-write-authorization.md) and
reverts on any of: the operator disabling it; a write landing outside the
envelope (the task auto-downgrades and flags); three consecutive runs below the
breaker floor; or store-triangulated blended efficiency falling below target
with paid identified as the driver.

On revert the autopilot **keeps running and reporting and stops writing**. The
worst possible response to a governance failure is to also lose visibility.
