# The Write Path

Agon ships a **live write path**. It can pause ads, move budget, and create ads
in a real account that spends real money. This document is the safety model.

If you read one section, read §2.

---

## 1. Why a live write path at all

A propose-only system is safer and, past a certain point, useless. The most
expensive failure in a governed ad account is not a bad automated move — it is
the proposal that was generated, reviewed, agreed, and never executed, while
winners sat unharvested and losers kept spending.

That loop is measurable. In the account this framework was built against, an
audit found weeks of correct proposals with no corresponding account changes,
winners stuck in discovery, a retirement stage running dark at 5.8× while
budget sat in stages returning under 1×. The proposals were right. Nothing
happened.

So Agon executes. The entire question is what stops it executing something
stupid.

---

## 2. Ten mechanisms, layered

No single one of these is trusted. Each assumes the ones above it have failed.

**1. Dry run is the default.** Every mutating path prints its exact request and
exits. `--confirm-write` is the only thing that dispatches, and it is never
implied by another flag.

**2. `AGON_READ_ONLY=1` overrides everything.** One environment variable forces
propose-only regardless of flags, config or arguments. It is checked inside the
dispatch function, not at the CLI boundary, so no code path routes around it.
This is the kill switch — set it in the shared environment while you
investigate anything.

**3. Server-side validation before every write.** Each dispatch first issues the
identical call with the platform's `validate_only` execution option, which runs
full server-side validation and creates nothing. A failed validation aborts
that one operation — logged as a FAILED outcome in the audit — and the run
continues with the remaining actions.

**4. Everything is born paused.** Every created entity — ad, ad set, campaign —
is created `PAUSED`. Activation is always a separate, separately-verified
write. A bug in the creation path therefore produces a dark ad, not a spending
one.

**5. The envelope is enforced in code.** `config.envelope.authorized` lists what
may execute without per-change confirmation. An action outside it is
**downgraded to a proposal and flagged** — not blocked with an error, not
silently dropped. The distinction matters: a downgrade keeps the reasoning
visible in the report, so the operator can execute it deliberately.

**6. The account allowlist lives in the adapter.** A write whose account ID is
not in `allowed_account_ids` raises before any HTTP call is composed. Config
alone is not trusted; the adapter checks independently.

**7. Budget steps are clamped in code.** Increases are capped at +30% per step
regardless of what config asks for. This is auction mechanics — a larger step
resets the learning phase — so it is not a policy the operator gets to raise.
Compounding daily steps are permitted; single large jumps are not.

**8. There is no delete verb.** Not in the adapters, not in the CLI, not
anywhere in the codebase. Retirement is always a pause. The entity ID and its
lifetime metrics are the audit anchor, and a deleted entity takes its own
history with it. The absence is enforced by a test.

**9. Post-write verification.** Every write is followed by a read-back. For
duplications this specifically verifies the **post ID survived** — the tell
that social proof carried across. A mismatch raises loudly rather than being
accepted as success.

**10. Append-only audit.** Every action, skip, downgrade and dry run is appended
to a JSONL log with its gate evidence, the council ruling if there was one, and
the response. Dry runs are logged too, because "what would it have done" is a
question you will want answered.

---

## 3. Guards sit above all of it

Before any of the above runs, [`gates.md` §10](gates.md#10-guards) applies:

- **Incomplete or failed pull ⇒ no writes this run.** Not a reduced set of
  writes. None.
- **Anomaly guard:** if the computed action set would pause entities
  representing more than half of recent pipeline spend, execute nothing and
  report urgently. A mass-kill signal indicates bad data far more often than it
  indicates bad ads.
- **Circuit breakers:** account return below the breaker floor, a broken pull,
  or a tripped anomaly guard each end the run at report-only.

The Risk Officer's hard veto is these guards. It is enforced in `guards.py`,
not in a prompt, so no amount of persuasive argument in a council round can
route around it.

---

## 4. Credentials

`META_ACCESS_TOKEN`, read from the environment. Never committed, never logged,
never included in a report — the audit log redacts anything token-shaped before
writing.

Use a **system user token** scoped to the one account, with the narrowest scopes
that work: `ads_management`, `ads_read`, and `pages_read_engagement` for post-ID
extraction. A personal user token expires on a 60-day clock and will strand an
unattended pipeline at the least convenient moment.

Set `allowed_account_ids` to exactly the accounts Agon may touch. It is checked
in the adapter on every write.

**The pixel is set explicitly on every ad set Agon creates.** Never rely on a
connector default. A mis-inherited pixel silently optimises against the wrong
event, and the damage is invisible for weeks because delivery looks entirely
normal.

---

## 5. Running it safely the first time

```bash
export AGON_READ_ONLY=1                      # belt
agon audit  --config account.yaml            # read-only: does the pull look right?
agon plan   --config account.yaml            # what would it do, and on what evidence?
agon debate --config account.yaml            # what is contested, and why?
```

Read a week of plans before you unset the kill switch. You are checking three
things: that the baselines look sane and are `computed` rather than `fallback`,
that the actions match what you would have done by hand, and that the contested
set is roughly one action in five.

Then, still deliberately:

```bash
unset AGON_READ_ONLY
agon apply --config account.yaml             # still a dry run — no --confirm-write
agon apply --config account.yaml --confirm-write
```

Start with a narrow envelope. `ad.pause` and `campaign.budget_decrease` only —
the actions whose worst case is spending less money. Widen it once the audit
log has a week of moves you agree with.

---

## 6. Reporting a security issue

See [`../SECURITY.md`](../SECURITY.md). Anything that could cause an
unauthorised write, route around `AGON_READ_ONLY`, or leak a token should be
reported privately rather than filed as a public issue.
