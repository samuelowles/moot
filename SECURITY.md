# Security Policy

Moot holds credentials that can spend money. Please read this before running it
against a live account, and before reporting an issue publicly.

## Reporting a vulnerability

**Do not open a public issue** for anything in the classes below. Report it
privately through GitHub's *Report a vulnerability* on the Security tab.

Please include reproduction steps and, if you can, the smallest config or
fixture that triggers it. You should get an acknowledgement within a few days.

### What counts as a vulnerability here

Anything that could cause an **unauthorised write** to an ad account, or leak a
credential:

- A path that dispatches a write without `confirm_write`.
- **Anything that routes around `MOOT_READ_ONLY=1`.** This is the kill switch;
  a bypass is the highest-severity class in this project.
- An action executing outside the configured envelope instead of being
  downgraded to a proposal.
- A write reaching an account not in `allowed_account_ids`.
- A budget step exceeding the +30% clamp.
- A token appearing in a log, report, audit entry, error message or traceback.
- A duplication that silently loses or changes a post ID without raising.
- Anything that makes the anomaly guard or a circuit breaker fail open.

Ordinary bugs — a gate computing the wrong number, a report rendering badly —
are normal public issues. When unsure, report privately; it is easy to move a
report into the open and impossible to move it back.

## The threat model

Moot is a **local operator tool**. It assumes the machine running it is trusted
and the person running it is authorised to manage the account. It is not a
multi-tenant service, has no authentication of its own, and should not be
exposed as a network service. If you wrap it in one, the wrapper owns
authentication, authorisation and rate limiting entirely.

The interesting boundary is not the network — it is between **model output and
account writes**. Moot's design assumption is that a language model may at any
point be confidently wrong, prompt-injected through ad names or creative copy
it reads, or simply hallucinating an entity ID. The mechanisms in
[`docs/writes.md`](docs/writes.md) exist so that none of those produce an
unauthorised write:

- The council layer **never calls a model** from Python. It prepares briefs and
  enforces vetoes. Model output re-enters the system only as a ruling on an
  action a gate already proposed — it cannot introduce a new action.
- The envelope, the account allowlist, the budget clamp and the kill switch are
  all enforced **in code**, below any prompt. No amount of persuasive model
  output routes around them.
- Two agent vetoes are mechanical (`guards.py`, `council.py`) rather than
  instructions in a charter, because an agent that can be argued out of a safety
  rule does not have one.

Treat any prose in a prompt or charter as advisory, and anything in
`writes.py` or `guards.py` as load-bearing. A change that moves a check from
the second category into the first is a security change even if it looks like a
refactor.

## Credentials

`META_ACCESS_TOKEN` is read from the environment and nowhere else. It is never
committed, never logged, and never written to a report — the audit writer
redacts token-shaped strings before serialising.

Recommended handling:

- Use a **system user token** scoped to the single account you intend to govern.
  A personal user token expires on a 60-day clock and will strand an unattended
  pipeline at the worst possible moment.
- Grant the narrowest scopes that work: `ads_management`, `ads_read`, and
  `pages_read_engagement` for post-ID extraction.
- Set `allowed_account_ids` to exactly the accounts Moot may touch. This is
  checked in the adapter on every write, independently of config validation.
- Rotate on any suspicion. There is no state in Moot that a rotation breaks.

`.env` is gitignored. `.env.example` is the only environment file in the
repository and contains no real values.

## Running against a live account for the first time

Set `MOOT_READ_ONLY=1` in the environment and leave it there while you read a
week of plans. Then start with the narrowest useful envelope — `ad.pause` and
`campaign.budget_decrease`, the actions whose worst case is spending less money
— and widen it only once the audit log contains a week of moves you agree with.

Full procedure: [`docs/writes.md`](docs/writes.md) §5.

## Supported versions

Pre-1.0. Fixes land on `main` only; there are no maintained release branches
yet.
