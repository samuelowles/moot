"""The PreToolUse hooks, run as the subprocesses Claude Code invokes.

The hooks are executable Python fed a JSON payload on stdin, signalling by
exit code (0 allow, 2 block). These tests run them exactly that way, because
a hook that fails closed on a parse error breaks the user's session — the
one failure mode worse than the risk the hook guards.

Malformed-input cases are the point: every payload below that is not a clean
``{"tool_input": {"command": ...}}`` object must still exit 0.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

#: Payloads that are NOT a well-formed PreToolUse object. Every one must
#: fail OPEN (exit 0): blocking the session on a parse error is worse than
#: the risk the hook guards.
MALFORMED_PAYLOADS = [
    "not json at all",
    "",
    "[1, 2, 3]",                # valid JSON, not an object
    '"a bare string"',          # valid JSON, not an object
    "null",                     # valid JSON, not an object
    '{"tool_input": [1, 2]}',   # tool_input is not an object
    '{"tool_input": "bash"}',
]

#: Matches the example config's naming convention (and the hook default).
COMPLIANT_NAME = "NZ-20260829-VID-HOOKS-TEST"

CONFIRM_WRITE_CMD = "moot apply --confirm-write --config account.yaml"

GUARD = "guard_writes.py"
VALIDATE = "validate_ad_name.py"


def _bash_payload(command: str, transcript_path: str | None = None) -> dict:
    payload: dict = {"tool_name": "Bash", "tool_input": {"command": command}}
    if transcript_path is not None:
        payload["transcript_path"] = transcript_path
    return payload


CUSTOM_CONFIG = """\
policy:
  naming:
    pattern: "^[a-z]+$"
  destination:
    forbid_patterns:
      - "/forbidden-zone/"
"""


# --- hooks.json: the wiring Claude Code reads -----------------------------------


def _pre_tool_use(plugin_root: Path) -> list[dict]:
    data = json.loads((plugin_root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    entries = data.get("hooks", {}).get("PreToolUse")
    assert isinstance(entries, list) and entries, (
        "hooks.json: no PreToolUse entries — the plugin would install no hooks"
    )
    return entries


def test_hooks_manifest_hook_commands_exist(plugin_root: Path) -> None:
    """After ${CLAUDE_PLUGIN_ROOT} substitution every referenced command
    path must exist, or the installed hook errors on every invocation."""
    for entry in _pre_tool_use(plugin_root):
        for hook in entry["hooks"]:
            command = hook["command"]
            assert "${CLAUDE_PLUGIN_ROOT}" in command, (
                f"hook command does not reference ${{CLAUDE_PLUGIN_ROOT}}: {command}"
            )
            substituted = command.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root))
            quoted = re.findall(r'"([^"]+)"', substituted)
            assert quoted, f"hook command has no quoted path to check: {command}"
            for path in quoted:
                assert Path(path).exists(), (
                    f"hook command references a missing file: {path} "
                    f"(from {command})"
                )


def test_every_hook_declares_matcher_and_timeout(plugin_root: Path) -> None:
    for entry in _pre_tool_use(plugin_root):
        matcher = entry.get("matcher")
        assert isinstance(matcher, str) and matcher.strip(), (
            f"PreToolUse entry has no matcher: {entry}"
        )
        for hook in entry["hooks"]:
            assert hook.get("type") == "command", f"unsupported hook type: {hook}"
            timeout = hook.get("timeout")
            assert isinstance(timeout, (int, float)) and timeout > 0, (
                f"hook has no positive timeout — a hung hook would stall every "
                f"matching tool call: {hook}"
            )


# --- guard_writes.py: the --confirm-write speed bump -----------------------------


def test_guard_blocks_confirm_write_with_no_plan_evidence(run_hook) -> None:
    result = run_hook(GUARD, _bash_payload(CONFIRM_WRITE_CMD))
    assert result.returncode == 2, (
        f"expected block (2), got {result.returncode}; stderr={result.stderr!r}"
    )
    assert "moot plan" in result.stderr, (
        "the block reason must name the command to run first"
    )


def test_guard_allows_confirm_write_when_plan_seen_in_transcript(
    run_hook, tmp_path: Path
) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        '{"role":"assistant","text":"pulling the snapshot"}\n'
        '{"role":"assistant","text":"ran moot plan --config account.yaml"}\n'
        '{"role":"assistant","text":"reviewing the actions"}\n',
        encoding="utf-8",
    )
    result = run_hook(GUARD, _bash_payload(CONFIRM_WRITE_CMD, str(transcript)))
    assert result.returncode == 0, (
        f"a reviewed plan is evidenced in the transcript; stderr={result.stderr!r}"
    )


@pytest.mark.parametrize("command", ["plan", "audit", "debate"])
def test_guard_allows_read_only_moot_commands(run_hook, command: str) -> None:
    result = run_hook(GUARD, _bash_payload(f"moot {command} --config account.yaml"))
    assert result.returncode == 0, (
        f"a read-only command must never be blocked; stderr={result.stderr!r}"
    )


def test_guard_allows_unrelated_commands(run_hook) -> None:
    result = run_hook(GUARD, _bash_payload("ls -la"))
    assert result.returncode == 0


@pytest.mark.parametrize("payload", MALFORMED_PAYLOADS)
def test_guard_fails_open_on_malformed_stdin(run_hook, payload: str) -> None:
    result = run_hook(GUARD, payload)
    assert result.returncode == 0, (
        f"malformed payload {payload!r} must fail open, not block or crash; "
        f"stderr={result.stderr!r}"
    )
    assert "Traceback" not in result.stderr


def test_guard_missing_tool_input_fails_open(run_hook) -> None:
    result = run_hook(GUARD, {"tool_name": "Bash"})
    assert result.returncode == 0


def test_guard_absent_transcript_falls_back_to_block(
    run_hook, tmp_path: Path
) -> None:
    """No plan evidenced by any means — including a transcript path that
    does not exist — means block, not crash."""
    missing = tmp_path / "no-such-transcript.jsonl"
    result = run_hook(GUARD, _bash_payload(CONFIRM_WRITE_CMD, str(missing)))
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


def test_guard_directory_transcript_falls_back_to_block(
    run_hook, tmp_path: Path
) -> None:
    result = run_hook(GUARD, _bash_payload(CONFIRM_WRITE_CMD, str(tmp_path)))
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


@pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="unreadable-by-mode is POSIX-only and root ignores file modes",
)
def test_guard_unreadable_transcript_blocks_without_crash(
    run_hook, tmp_path: Path
) -> None:
    transcript = tmp_path / "locked.jsonl"
    transcript.write_text("ran moot plan\n", encoding="utf-8")
    transcript.chmod(0)  # no permission bits: reads now fail
    result = run_hook(GUARD, _bash_payload(CONFIRM_WRITE_CMD, str(transcript)))
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


# --- validate_ad_name.py: naming and destination policy --------------------------


def test_validate_allows_compliant_name(run_hook) -> None:
    result = run_hook(VALIDATE, _bash_payload(f"moot apply --name {COMPLIANT_NAME}"))
    assert result.returncode == 0, f"stderr={result.stderr!r}"


def test_validate_blocks_non_compliant_name_and_shows_pattern(run_hook) -> None:
    result = run_hook(VALIDATE, _bash_payload("moot apply --name summer-sale"))
    assert result.returncode == 2
    assert "^[A-Z]{2,6}-[0-9]{8}" in result.stderr, (
        "the block reason must show the configured pattern so the name can be fixed"
    )


def test_validate_blocks_collections_destination(run_hook) -> None:
    command = f"moot apply --name {COMPLIANT_NAME} " \
              "--url https://shop.example.com/collections/all"
    result = run_hook(VALIDATE, _bash_payload(command))
    assert result.returncode == 2
    assert "destination policy" in result.stderr


def test_validate_allows_products_destination(run_hook) -> None:
    command = f"moot apply --name {COMPLIANT_NAME} " \
              "--url https://shop.example.com/products/widget"
    result = run_hook(VALIDATE, _bash_payload(command))
    assert result.returncode == 0, f"stderr={result.stderr!r}"


def test_validate_ignores_commands_that_create_nothing(run_hook) -> None:
    result = run_hook(VALIDATE, _bash_payload("moot plan --name 'freeform text'"))
    assert result.returncode == 0, (
        "only apply/duplicate carry a name and destination worth checking"
    )


@pytest.mark.parametrize("payload", MALFORMED_PAYLOADS)
def test_validate_fails_open_on_malformed_stdin(run_hook, payload: str) -> None:
    result = run_hook(VALIDATE, payload)
    assert result.returncode == 0, (
        f"malformed payload {payload!r} must fail open, not block or crash; "
        f"stderr={result.stderr!r}"
    )
    assert "Traceback" not in result.stderr


def _custom_config(tmp_path: Path) -> Path:
    path = tmp_path / "custom-policy.yaml"
    path.write_text(CUSTOM_CONFIG, encoding="utf-8")
    return path


def test_validate_pattern_comes_from_the_config_file(
    run_hook, tmp_path: Path
) -> None:
    """The pattern on the command line governs, not the default: a name the
    default accepts is blocked under a config whose pattern differs, and a
    name only that config accepts passes."""
    config = _custom_config(tmp_path)
    strict = run_hook(
        VALIDATE,
        _bash_payload(f"moot apply --config {config} --name {COMPLIANT_NAME}"),
    )
    assert strict.returncode == 2, (
        "the custom pattern ^[a-z]+$ must reject the uppercase default shape"
    )
    relaxed = run_hook(
        VALIDATE,
        _bash_payload(f"moot apply --config {config} --name lowercase"),
    )
    assert relaxed.returncode == 0, (
        f"the custom pattern ^[a-z]+$ must accept 'lowercase'; "
        f"stderr={relaxed.stderr!r}"
    )


def test_validate_forbidden_fragment_comes_from_the_config_file(
    run_hook, tmp_path: Path
) -> None:
    config = _custom_config(tmp_path)
    command = (
        f"moot apply --config {config} --name lowercase "
        "--url https://shop.example.com/forbidden-zone/x"
    )
    result = run_hook(VALIDATE, _bash_payload(command))
    assert result.returncode == 2, (
        "the custom forbidden fragment /forbidden-zone/ must block a URL the "
        "default policy would allow"
    )
    assert "destination policy" in result.stderr


def test_validate_example_config_governs(run_hook) -> None:
    """A name valid under examples/config.example.yaml passes when that file
    is on the command line, exactly as the documented commands run."""
    command = (
        "moot apply --config examples/config.example.yaml "
        f"--name {COMPLIANT_NAME}"
    )
    result = run_hook(VALIDATE, _bash_payload(command))
    assert result.returncode == 0, f"stderr={result.stderr!r}"


def test_validate_without_config_flag_uses_defaults(run_hook) -> None:
    compliant = run_hook(VALIDATE, _bash_payload(f"moot apply --name {COMPLIANT_NAME}"))
    assert compliant.returncode == 0
    rejected = run_hook(VALIDATE, _bash_payload("moot apply --name lowercase"))
    assert rejected.returncode == 2, (
        "with no --config the hook must fall back to its default pattern"
    )
