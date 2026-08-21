#!/usr/bin/env python3
"""PreToolUse hook: block ad creation whose name violates the configured
naming convention, and whose destination violates the destination policy.

Why this exists
---------------
Naming is not cosmetic. Every downstream report — concept-level aggregation,
per-market cart rates, the creative-supply warning — parses structure out of ad
names. One unparseable name is one ad that silently drops out of every
concept-level gate, and concept-level gates are where the cart-rate kill limb
lives.

Scope and its limits
--------------------
This hook fires on interactive `agon` invocations in a Claude Code session. It
does **not** fire on headless scheduled runs, which never pass through a
PreToolUse hook at all. The same two checks are therefore implemented
in-process in `src/agon/writes.py`, and the two implementations must be kept in
sync. If you change the pattern here, change it there.

Treat the in-process check as the guarantee and this hook as the fast feedback.

Exit codes
----------
0  allow
2  block, with the reason on stderr
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - the hook must degrade, never crash
    yaml = None

DEFAULT_PATTERN = r"^[A-Z]{2,6}-[0-9]{8}-(VID|SVID|CAR|IMG)-[A-Z0-9-]{2,12}-[A-Z0-9-]{2,12}$"
DEFAULT_FORBIDDEN = ("/collections/",)

# Only creation carries a name and a destination worth checking.
CREATES_AD = re.compile(r"\bagon\s+(apply|duplicate)\b")
NAME_FLAG = re.compile(r"--name[= ]\"?([^\"\s]+)")
URL_FLAG = re.compile(r"--(?:url|destination|website-url)[= ]\"?([^\"\s]+)")
CONFIG_FLAG = re.compile(r"--config[= ]\"?([^\"\s]+)")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    if not CREATES_AD.search(command):
        return 0

    pattern, forbidden = _policy(command)
    problems: list[str] = []

    name_match = NAME_FLAG.search(command)
    if name_match and not re.match(pattern, name_match.group(1)):
        problems.append(
            f"Ad name {name_match.group(1)!r} does not match the configured "
            f"convention:\n    {pattern}\n"
            "  Unparseable names drop the ad out of every concept-level gate."
        )

    url_match = URL_FLAG.search(command)
    if url_match:
        url = url_match.group(1)
        hit = next((frag for frag in forbidden if frag in url), None)
        if hit:
            problems.append(
                f"Destination {url!r} contains {hit!r}, which the account's "
                "destination policy forbids.\n"
                "  This is the Brand Steward's hard veto — see docs/agents.md."
            )

    if not problems:
        return 0

    sys.stderr.write("Blocked before dispatch:\n\n" + "\n\n".join(problems) + "\n")
    return 2


def _policy(command: str) -> tuple[str, tuple[str, ...]]:
    """Read pattern and forbidden fragments from the account config.

    Falls back to defaults on any failure. A hook that cannot read a config is
    a hook that must still let a correct command through.
    """
    if yaml is None:
        return DEFAULT_PATTERN, DEFAULT_FORBIDDEN

    match = CONFIG_FLAG.search(command)
    if not match:
        return DEFAULT_PATTERN, DEFAULT_FORBIDDEN

    path = Path(match.group(1))
    if not path.is_file():
        return DEFAULT_PATTERN, DEFAULT_FORBIDDEN

    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return DEFAULT_PATTERN, DEFAULT_FORBIDDEN

    policy = (config.get("policy") or {})
    pattern = (policy.get("naming") or {}).get("pattern", DEFAULT_PATTERN)
    forbidden = (policy.get("destination") or {}).get("forbid_patterns", DEFAULT_FORBIDDEN)
    return pattern, tuple(forbidden)


if __name__ == "__main__":
    sys.exit(main())
