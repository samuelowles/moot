#!/usr/bin/env python3
"""PreToolUse hook: refuse an interactive `moot apply --confirm-write` that has
not been preceded by a plan in the same session.

Why this exists
---------------
The Python layer already enforces dry-run-by-default, the MOOT_READ_ONLY kill
switch, the authorization envelope and the +30% budget clamp (see
docs/writes.md). Those are the real guarantees, and none of them depend on this
hook.

This hook defends a different failure: an agent that reasons its way to
`--confirm-write` without ever having looked at the plan. That is not a code
path the runtime can detect — every individual call is legitimate — so it is
caught at the point of dispatch instead.

It is a speed bump, deliberately. It blocks the flag, explains what to run
first, and never blocks a read.

Exit codes
----------
0  allow
2  block, with the reason on stderr (Claude Code surfaces it to the model)
"""

from __future__ import annotations

import json
import re
import sys
from collections import deque
from pathlib import Path

# A plan is considered "seen" when `moot plan` has run in this session. The
# transcript is the only session-scoped evidence available to a hook.
PLAN_EVIDENCE = re.compile(r"\bmoot\s+(plan|audit|debate)\b")
CONFIRM_WRITE = re.compile(r"\bmoot\s+apply\b(?=.*--confirm-write)")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # A hook that cannot parse its input must not block the session.
        return 0

    if not isinstance(payload, dict):
        # Valid JSON that is not an object (an array, a string, null) carries
        # no tool_input to inspect — fail open on the shape, as on the parse.
        return 0

    tool_input = payload.get("tool_input")
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not isinstance(command, str):
        return 0
    if not CONFIRM_WRITE.search(command):
        return 0

    if _plan_seen(payload.get("transcript_path")):
        return 0

    sys.stderr.write(
        "Blocked: `moot apply --confirm-write` dispatches live writes to a real "
        "ad account, and no plan has been reviewed in this session.\n\n"
        "Run `moot plan --config <config>` first and read the actions and their "
        "gate evidence. If any action is contested, run `moot debate` before "
        "executing.\n\n"
        "To dispatch anyway, run the command in a session where the plan has "
        "been reviewed, or set MOOT_READ_ONLY=1 to confirm the pipeline is "
        "wired correctly without spending anything.\n"
    )
    return 2


def _plan_seen(transcript_path: str | None) -> bool:
    """True when this session has already run a read-only moot command."""
    if not transcript_path:
        return False
    path = Path(transcript_path)
    if not path.is_file():
        return False
    try:
        # Transcripts are JSONL and can be long; only the tail matters and a
        # read failure must never block the session.
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            tail = deque(handle, maxlen=400)
    except OSError:
        return False
    return any(PLAN_EVIDENCE.search(line) for line in tail)


if __name__ == "__main__":
    sys.exit(main())
