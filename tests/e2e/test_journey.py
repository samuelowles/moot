"""A full install-to-run journey, as subprocesses from a foreign cwd.

Every command here runs from a temporary working directory, not the repo
root, so anything that works only because of the working directory — a
defaulted fixture path, a config resolved relative to the checkout — fails
here instead of in a user's terminal.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

_DISPATCHED_COUNT = re.compile(r"dispatched: \d+")
_MARKET_LINE = re.compile(r"^(NZ|AU|US): ", re.MULTILINE)


@pytest.fixture()
def journey(run_cli, example_config: Path, fixtures_dir: Path, tmp_path: Path):
    """The documented invocation shape: connection options after the
    subcommand, absolute paths, cwd outside the repo."""

    def _run(*args: str, env_extra: dict[str, str] | None = None):
        return run_cli(
            "--config", str(example_config),
            "--fixtures", str(fixtures_dir),
            *args,
            cwd=tmp_path,
            env_extra=env_extra,
        )

    return _run


def test_help_exits_zero(journey) -> None:
    result = journey("--help")
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert "Usage:" in result.stdout


def test_audit_reports_the_run(journey) -> None:
    result = journey("audit")
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert result.stdout.strip(), "audit printed nothing"
    assert "Agon run report" in result.stdout


def test_plan_reports_baselines_without_writing(journey) -> None:
    result = journey("plan")
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert result.stdout.strip(), "plan printed nothing"
    assert "Agon run report" in result.stdout
    assert "## Baselines" in result.stdout
    assert "nothing dispatched" in result.stdout


def test_baseline_reports_every_market(journey) -> None:
    result = journey("baseline")
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert result.stdout.strip(), "baseline printed nothing"
    markets = {m.group(1) for m in _MARKET_LINE.finditer(result.stdout)}
    assert {"NZ", "AU", "US"} <= markets, (
        f"baseline must state value and source for every configured market; "
        f"found {sorted(markets)}"
    )


def test_debate_prints_round_zero_briefs_with_numbers(journey) -> None:
    result = journey("debate")
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert result.stdout.strip(), "debate printed nothing"
    assert "Round 0" in result.stdout
    assert "Account target T" in result.stdout, (
        "the shared brief must carry the account target — a brief without "
        "numbers cannot be adjudicated"
    )


def test_apply_without_confirm_write_dispatches_nothing(journey) -> None:
    result = journey("apply")
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert "nothing dispatched" in result.stdout
    assert "## Dispatch" in result.stdout
    assert "dry-run: " in result.stdout


def test_read_only_beats_confirm_write(journey) -> None:
    result = journey("apply", "--confirm-write", env_extra={"AGON_READ_ONLY": "1"})
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert "skipped-readonly" in result.stdout
    assert _DISPATCHED_COUNT.search(result.stdout) is None, (
        "the kill switch must prevent every dispatch, not just flag it"
    )


def test_confirm_write_dispatches_and_audits(journey, tmp_path: Path) -> None:
    result = journey("apply", "--confirm-write")
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert _DISPATCHED_COUNT.search(result.stdout), (
        "the fixture adapter should have dispatched the computed action set"
    )
    audit = tmp_path / "reports" / "write-audit.jsonl"
    assert audit.is_file(), (
        f"dispatch must leave an append-only audit trail at {audit}"
    )
    lines = [line for line in audit.read_text(encoding="utf-8").splitlines() if line]
    assert lines, "the audit log is empty"
    for line in lines:
        entry = json.loads(line)  # every line must parse as JSON
        assert entry.get("run_id"), f"audit entry carries no run_id: {line[:120]}"


def test_guard_trip_exits_non_zero(run_cli, example_config: Path, repo_root: Path,
                                   tmp_path: Path) -> None:
    result = run_cli(
        "--config", str(example_config),
        "--fixtures", str(repo_root / "tests" / "fixtures" / "incomplete"),
        "apply", "--confirm-write",
        cwd=tmp_path,
    )
    assert result.returncode != 0, "a tripped guard must be visible to cron"
    assert "GUARD TRIP" in result.stdout


def test_invalid_config_path_fails_cleanly(run_cli, tmp_path: Path) -> None:
    result = run_cli("--config", str(tmp_path / "no-such-config.yaml"), "plan",
                     cwd=tmp_path)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, "an operator error is not a crash"
    assert "Error" in combined, "the failure must say what went wrong"
    assert "no-such-config.yaml" in combined


def _console_scripts(repo_root: Path) -> dict[str, str]:
    """The [project.scripts] mapping, read textually (no tomllib on 3.10)."""
    scripts: dict[str, str] = {}
    in_scripts = False
    for line in (repo_root / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("["):
            in_scripts = line.strip() == "[project.scripts]"
            continue
        if in_scripts and "=" in line:
            name, _, target = line.partition("=")
            scripts[name.strip()] = target.strip().strip('"')
    return scripts


def test_console_script_entry_points_resolve(repo_root: Path) -> None:
    """Every declared console script must name an importable callable — an
    entry point pointing at a typo'd module fails only at install time."""
    scripts = _console_scripts(repo_root)
    assert scripts, "pyproject.toml declares no [project.scripts]"
    for name, target in scripts.items():
        module, _, attr = target.partition(":")
        loaded = importlib.import_module(module)
        assert callable(getattr(loaded, attr, None)), (
            f"console script {name!r} -> {target!r} does not resolve to a callable"
        )
