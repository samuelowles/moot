---
description: Create, inspect or regenerate the Moot scheduled tasks (daily review and pipeline autopilot).
argument-hint: review | autopilot | status | regenerate
allowed-tools: Bash, Read, Grep, Glob
---

Manage the Moot scheduled tasks. Argument: `${1:-status}`.

## `review` / `autopilot`

Render the matching template from `plugin/scheduled-tasks/` with the account's
config values, then create a Claude Code scheduled task from the rendered
prompt.

Interpolate `{{ACCOUNT_NAME}}`, `{{CONFIG_PATH}}`, `{{TARGET}}`, `{{MARKETS}}`,
`{{REPORT_SINK}}` and `{{TIMEZONE}}` from the account config. Do not leave a
placeholder unresolved: an unrendered `{{TARGET}}` in a live prompt produces
an agent inventing its own target.

Set the cron from the template's `suggested_local_time` converted to UTC for the
account timezone, and **record the intended local time in the task
description**. Scheduled tasks fire on fixed UTC cron; daylight saving is not
handled, so the intended time is the only record of what the cron was meant to
mean.

Prefer a fire time at least two hours after local midnight, so a one-hour DST
drift cannot cross a day boundary and silently change which data day the run
analyses.

## `status`

List both tasks and report, for each: the cron, the local time it currently
fires, the local time it was *intended* to fire, and the `gates_version` its
prompt was generated from.

Flag two kinds of drift explicitly:

- **Time drift** — intended and actual local time have diverged. Daylight
  saving moved underneath the cron. The task still succeeds; it just runs at
  the wrong hour, which matters when the contract assumes a full previous day.
- **Gate drift** — the prompt's `gates_version` is behind the repo's. The
  documented gates and the running gates are different things. See
  `docs/scheduling.md` §3.

## `regenerate`

Re-render both prompts from the current templates and config, and confirm the
`gates_version` stamp moved.

Do this after any gate change. A headless run cannot read this repository:
editing `docs/gates.md` or the config changes nothing about what the scheduled
tasks do until their prompts are regenerated. An ADR that revises a threshold
has changed the documentation and nothing else.

Show the diff between old and new prompt before creating the replacement, and
say plainly which thresholds moved.
