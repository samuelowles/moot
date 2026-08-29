"""Manifest integrity — the marketplace/plugin pair a `/plugin install` reads.

A placeholder URL or a version that drifted from the package is invisible
until someone clicks it, so the manifests are checked like code.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MARKETPLACE_KEYS = ("name", "owner", "plugins")
PLUGIN_KEYS = ("name", "version", "description", "license")


def _load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name}: top level is not a JSON object"
    return data


def _pyproject_version(repo_root: Path) -> str:
    """The [project] version — read textually: tomllib is 3.11+ and the suite
    must run on 3.10 with no added dependencies."""
    text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    in_project = False
    for line in text.splitlines():
        if line.startswith("["):
            in_project = line.strip() == "[project]"
            continue
        match = re.match(r'^version\s*=\s*"([^"]+)"', line)
        if in_project and match:
            return match.group(1)
    raise AssertionError("pyproject.toml: no version under [project]")


def test_marketplace_parses_with_required_keys(repo_root: Path) -> None:
    marketplace = _load_json(repo_root / ".claude-plugin" / "marketplace.json")
    for key in MARKETPLACE_KEYS:
        assert marketplace.get(key), f"marketplace.json: missing {key!r}"
    assert isinstance(marketplace["plugins"], list) and marketplace["plugins"], (
        "marketplace.json: plugins must be a non-empty list"
    )


def test_marketplace_plugin_sources_exist(repo_root: Path) -> None:
    marketplace = _load_json(repo_root / ".claude-plugin" / "marketplace.json")
    for entry in marketplace["plugins"]:
        source = entry["source"]
        assert (repo_root / source).resolve().is_dir(), (
            f"marketplace.json: plugins[].source {source!r} does not exist "
            "relative to the repo root"
        )


def test_plugin_manifest_has_required_keys(plugin_root: Path) -> None:
    manifest = _load_json(plugin_root / ".claude-plugin" / "plugin.json")
    for key in PLUGIN_KEYS:
        value = manifest.get(key)
        assert isinstance(value, str) and value.strip(), (
            f"plugin.json: {key!r} must be a non-empty string"
        )


def test_plugin_version_matches_pyproject(repo_root: Path, plugin_root: Path) -> None:
    manifest = _load_json(plugin_root / ".claude-plugin" / "plugin.json")
    assert manifest["version"] == _pyproject_version(repo_root), (
        "plugin.json version has drifted from pyproject.toml — the marketplace "
        "would advertise a version the package does not ship"
    )


def test_no_username_placeholder_in_plugin_manifest(plugin_root: Path) -> None:
    raw = (plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    assert "USERNAME" not in raw, (
        "plugin.json still carries a USERNAME placeholder — a template that "
        "was never filled in ships to every install"
    )


def test_no_username_placeholder_in_marketplace(repo_root: Path) -> None:
    raw = (repo_root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    assert "USERNAME" not in raw, (
        "marketplace.json still carries a USERNAME placeholder — a template "
        "that was never filled in ships to every install"
    )


def test_plugin_name_matches_marketplace_entry(
    repo_root: Path, plugin_root: Path
) -> None:
    marketplace = _load_json(repo_root / ".claude-plugin" / "marketplace.json")
    manifest = _load_json(plugin_root / ".claude-plugin" / "plugin.json")
    entries = {entry["name"] for entry in marketplace["plugins"]}
    assert manifest["name"] in entries, (
        f"plugin.json name {manifest['name']!r} is not a marketplace entry "
        f"({sorted(entries)}) — `/plugin install` resolves by that name"
    )
