"""The scheduled-task templates — what a headless run is created from.

A rendered prompt is the runtime authority (docs/scheduling.md §3): a
headless agent cannot read this repository. An undocumented placeholder ships
unrendered and the agent then invents its own target; a propose-only template
that authorises writes defeats the ADR-0002 revert condition.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REQUIRED_FIELDS = (
    "name",
    "description",
    "type",
    "authority",
    "suggested_local_time",
    "gates_version",
)

_PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def _templates(plugin_root: Path) -> list[Path]:
    return sorted((plugin_root / "scheduled-tasks").glob("*.md"))


def _template_paths() -> list[Path]:
    root = Path(__file__).resolve().parent.parent.parent / "plugin" / "scheduled-tasks"
    return sorted(root.glob("*.md"))


_TEMPLATE_IDS = [p.name for p in _template_paths()]


def _documented_placeholders(path: Path) -> set[str]:
    """The placeholders a template enumerates in its own prose — the
    "Placeholders: `{{X}}`, `{{Y}}`." sentence, which may wrap lines."""
    text = path.read_text(encoding="utf-8")
    marker = text.find("Placeholders:")
    assert marker >= 0, f"{path.name}: no documented placeholder list"
    sentence = text[marker : text.find(".", marker)]
    return set(_PLACEHOLDER.findall(sentence))


@pytest.mark.parametrize("template", _template_paths(), ids=_TEMPLATE_IDS)
def test_template_frontmatter_is_complete(template: Path, frontmatter) -> None:
    meta = frontmatter(template)
    for field in REQUIRED_FIELDS:
        value = meta.get(field)
        assert isinstance(value, str) and value.strip(), (
            f"{template.name}: frontmatter {field!r} must be a non-empty string"
        )


def test_authority_declarations(plugin_root: Path, frontmatter) -> None:
    """The pair is the design: a propose-only review and an execute-in-
    envelope autopilot (ADR-0002's revert keeps the review, drops the
    writes). Swapping either authority silently changes what runs unattended."""
    templates = {p.name: frontmatter(p) for p in _templates(plugin_root)}
    assert templates, "plugin/scheduled-tasks/ contains no templates"
    assert templates["daily-review.md"]["authority"] == "propose-only", (
        "daily-review must declare propose-only authority"
    )
    assert templates["pipeline-autopilot.md"]["authority"] == "execute-in-envelope", (
        "pipeline-autopilot must declare execute-in-envelope authority"
    )


@pytest.mark.parametrize("template", _template_paths(), ids=_TEMPLATE_IDS)
def test_every_placeholder_is_documented(template: Path) -> None:
    """Every {{PLACEHOLDER}} in the prompt body must appear in that file's
    own documented list — an undocumented one renders literally and the
    headless agent invents a value for it."""
    documented = _documented_placeholders(template)
    body = template.read_text(encoding="utf-8")
    used = set(_PLACEHOLDER.findall(body))
    undocumented = used - documented
    assert not undocumented, (
        f"{template.name}: placeholder(s) {sorted(undocumented)} appear in the "
        f"prompt but are not in the documented list {sorted(documented)}"
    )


def test_propose_only_template_disclaims_write_authority(plugin_root: Path) -> None:
    text = (plugin_root / "scheduled-tasks" / "daily-review.md").read_text(
        encoding="utf-8"
    )
    assert re.search(r"(?i)propose\s+only", text), (
        "the daily-review template must state its PROPOSE ONLY authority"
    )


def test_propose_only_template_never_authorises_writes(plugin_root: Path) -> None:
    text = (plugin_root / "scheduled-tasks" / "daily-review.md").read_text(
        encoding="utf-8"
    )
    assert "--confirm-write" not in text, (
        "the propose-only template must not instruct a confirmed write dispatch"
    )
    assert "moot apply" not in text, (
        "the propose-only template must not instruct the write command at all"
    )


@pytest.mark.parametrize("template", _template_paths(), ids=_TEMPLATE_IDS)
def test_template_states_the_incomplete_pull_rule(template: Path) -> None:
    """The single most important operational rule: no writes on a partial
    pull. Both halves of the pair must carry it — the review to report the
    gap, the autopilot to refuse to act on one."""
    text = template.read_text(encoding="utf-8")
    assert re.search(r"(?i)incomplete", text), (
        f"{template.name}: does not mention the incomplete-pull rule"
    )
    if template.name == "pipeline-autopilot.md":
        # The rule may wrap lines; it must still tie the incomplete pull to
        # NO WRITES on the same statement.
        assert re.search(r"(?i)incomplete[\s\S]{0,120}?no\s+writes", text), (
            "the autopilot must pair an incomplete pull with NO WRITES THIS RUN"
        )
