"""Shared fixtures for the end-to-end suite.

The unit suite covers the Python runtime; these exercise the plugin layer
the way Claude Code itself does — hooks as subprocesses fed a JSON PreToolUse
payload on stdin, the CLI as a subprocess from an arbitrary working directory.
Two real defects have already shipped in this layer and been caught by hand
(the agent roster drifted to names no charter file defined; a documented brief
did not match the rendered one); the point of this suite is to make that class
of failure impossible to ship again.

``tests/conftest.py`` (the parent conftest, loaded for this package too) pins
this checkout's ``src/`` ahead of any editable install, so in-process imports
of :mod:`moot.council` and :mod:`moot.writes` test this tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Union

import pytest
import yaml

E2E_DIR = Path(__file__).resolve().parent
REPO_ROOT = E2E_DIR.parent.parent
SRC_ROOT = REPO_ROOT / "src"

HookPayload = Union[dict[str, Any], str, None]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test under tests/e2e so `pytest -m "not e2e"` runs the unit
    suite alone (pyproject.toml registers the marker)."""
    for item in items:
        if E2E_DIR in item.path.parents:
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The checkout under test, independent of the process cwd."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def plugin_root(repo_root: Path) -> Path:
    """The Claude Code plugin root — what ${CLAUDE_PLUGIN_ROOT} expands to."""
    return repo_root / "plugin"


@pytest.fixture(scope="session")
def example_config(repo_root: Path) -> Path:
    """The worked example config every documented command runs against."""
    return repo_root / "examples" / "config.example.yaml"


@pytest.fixture(scope="session")
def fixtures_dir(repo_root: Path) -> Path:
    """The offline demo account — no network, no credentials."""
    return repo_root / "tests" / "fixtures"


@pytest.fixture(scope="session")
def frontmatter():
    """Parse the YAML frontmatter block at the top of a markdown file.

    Raises ValueError (with the filename in the message) when the block is
    missing or unterminated, so a charter or template that loses its
    frontmatter fails loudly rather than silently returning {}.
    """

    def _parse(path: Path) -> dict[str, Any]:
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError(f"{path.name}: no frontmatter block")
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                data = yaml.safe_load("\n".join(lines[1:index])) or {}
                if not isinstance(data, dict):
                    raise ValueError(f"{path.name}: frontmatter is not a mapping")
                return data
        raise ValueError(f"{path.name}: unterminated frontmatter block")

    return _parse


def _hook_env() -> dict[str, str]:
    env = os.environ.copy()
    # The report text the CLI prints carries Δ / § / — glyphs; a host console
    # in a non-UTF-8 codepage (cp1252) must not turn the house voice into a
    # crash. UTF-8 is the default on Linux; this only pins it elsewhere.
    env["PYTHONIOENCODING"] = "utf-8"
    return env


@pytest.fixture(scope="session")
def run_hook(plugin_root: Path):
    """Run one hook exactly the way Claude Code invokes it: a subprocess with
    the JSON PreToolUse payload on stdin, signalling by exit code.

    ``payload`` may be a dict (JSON-encoded onto stdin), a str (sent verbatim,
    for malformed-input cases), or None (empty stdin).
    """

    def _run(
        hook_name: str, payload: HookPayload, cwd: Optional[Path] = None
    ) -> subprocess.CompletedProcess[str]:
        stdin = "" if payload is None else (
            payload if isinstance(payload, str) else json.dumps(payload)
        )
        return subprocess.run(
            [sys.executable, str(plugin_root / "hooks" / hook_name)],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd or REPO_ROOT),
            env=_hook_env(),
            timeout=60,
        )

    return _run


@pytest.fixture(scope="session")
def run_cli():
    """Run `python -m moot.cli` as a subprocess from an arbitrary cwd.

    ``cwd`` is required, not defaulted to the repo root — the journey tests
    exist to catch anything that only works because of the working directory.
    """

    def _run(
        *args: str, cwd: Path, env_extra: Optional[dict[str, str]] = None
    ) -> subprocess.CompletedProcess[str]:
        env = _hook_env()
        # Pin THIS checkout's src/ ahead of any editable install pointing at
        # another worktree — the same guarantee tests/conftest.py makes for
        # in-process imports, applied to the subprocess.
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(SRC_ROOT), env.get("PYTHONPATH")) if p
        )
        # The kill switch must be off unless a test sets it explicitly.
        env.pop("MOOT_READ_ONLY", None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-m", "moot.cli", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd),
            env=env,
            timeout=180,
        )

    return _run
