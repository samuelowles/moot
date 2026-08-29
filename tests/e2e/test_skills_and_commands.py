"""Skills, commands and the README that promises them.

A README that references a command file nobody renamed-checks, an
``allowed-tools`` typo that silently disables a command, a relative doc link
broken by a file move — none of these fail at import time. They ship, and the
first person to notice is a user.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

#: The Claude Code tool names a plugin may allow. A typo here (``Task`` →
#: ``Tasks``) silently disables the command rather than erroring.
KNOWN_TOOLS = {
    "Bash",
    "Read",
    "Grep",
    "Glob",
    "Task",
    "Write",
    "Edit",
    "WebSearch",
    "WebFetch",
}

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PLUGIN_ROOT = _REPO_ROOT / "plugin"

_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)[^)]*\)")
_README_COMMAND = re.compile(r"/agon:(\w+)")

#: Every document whose relative links are checked: README, docs, the ADRs,
#: and the plugin's own markdown.
_LINKED_DOCS = [
    _REPO_ROOT / "README.md",
    *sorted((_REPO_ROOT / "docs").glob("*.md")),
    *sorted((_REPO_ROOT / "docs" / "adr").glob("*.md")),
    *sorted(_PLUGIN_ROOT.rglob("*.md")),
]


def test_skills_have_name_and_description(plugin_root: Path, frontmatter) -> None:
    skills = sorted((plugin_root / "skills").glob("*/SKILL.md"))
    assert skills, "plugin/skills/ contains no SKILL.md files"
    for path in skills:
        meta = frontmatter(path)
        for key in ("name", "description"):
            value = meta.get(key)
            assert isinstance(value, str) and value.strip(), (
                f"{path.parent.name}/SKILL.md: frontmatter {key!r} must be a "
                "non-empty string"
            )


def test_skill_name_matches_directory(plugin_root: Path, frontmatter) -> None:
    for path in sorted((plugin_root / "skills").glob("*/SKILL.md")):
        assert frontmatter(path)["name"] == path.parent.name, (
            f"{path.parent.name}/SKILL.md: name must equal the skill directory "
            "— Claude Code resolves skills by directory"
        )


def test_commands_have_description(plugin_root: Path, frontmatter) -> None:
    commands = sorted((plugin_root / "commands").glob("*.md"))
    assert commands, "plugin/commands/ contains no command files"
    for path in commands:
        value = frontmatter(path).get("description")
        assert isinstance(value, str) and value.strip(), (
            f"{path.name}: frontmatter 'description' must be a non-empty string"
        )


def test_readme_referenced_commands_exist(repo_root: Path, plugin_root: Path) -> None:
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    referenced = set(_README_COMMAND.findall(readme))
    assert referenced, "README.md references no /agon:<command> at all"
    for word in sorted(referenced):
        assert (plugin_root / "commands" / f"{word}.md").is_file(), (
            f"README.md references /agon:{word} but plugin/commands/{word}.md "
            "does not exist"
        )


def test_allowed_tools_within_known_set(plugin_root: Path, frontmatter) -> None:
    """``allowed-tools`` values, wherever present, must be real Claude Code
    tool names — a typo'd name silently revokes the tool."""
    checked = 0
    for path in sorted(plugin_root.rglob("*.md")):
        if not _has_frontmatter(path):
            continue
        allowed = frontmatter(path).get("allowed-tools")
        if not allowed:
            continue
        tools = [tool.strip() for tool in str(allowed).split(",") if tool.strip()]
        assert tools, f"{path.name}: allowed-tools is present but empty"
        unknown = set(tools) - KNOWN_TOOLS
        assert not unknown, (
            f"{path.name}: allowed-tools names unknown tools {sorted(unknown)} "
            f"— expected a subset of {sorted(KNOWN_TOOLS)}"
        )
        checked += 1
    assert checked, (
        "no file declares allowed-tools — the known-tool check covered nothing"
    )


def _has_frontmatter(path: Path) -> bool:
    first = path.read_text(encoding="utf-8").splitlines()[:1]
    return bool(first) and first[0].strip() == "---"


@pytest.mark.parametrize(
    "doc", _LINKED_DOCS, ids=[str(p.relative_to(_REPO_ROOT)) for p in _LINKED_DOCS]
)
def test_relative_markdown_links_resolve(doc: Path) -> None:
    """Every relative markdown link resolves to a file that exists. Anchor
    links (#section) have only their file part checked — validating GitHub's
    slug algorithm is not worth the coupling. Parametrized per document so one
    moved file cannot hide behind an aggregate pass."""
    broken = []
    for match in _LINK.finditer(doc.read_text(encoding="utf-8")):
        target = match.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        file_part = target.split("#")[0]
        if file_part and not (doc.parent / file_part).resolve().exists():
            broken.append(target)
    assert not broken, f"{doc}: broken relative link(s) {broken}"
