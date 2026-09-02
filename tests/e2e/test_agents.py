"""The agent charters vs the runtime roster — the drift catchers.

The roster in ``src/moot/council.py`` and the charter files in
``plugin/agents/`` are two views of one council. They once drifted apart (the
roster carried names no charter file defined), and nothing failed because no
test compared them. These tests do, from both directions.
"""

from __future__ import annotations

import re
from pathlib import Path

from moot.council import AGENT_ROSTER

#: council.py defines the adjudicator separately from the five-way roster.
ADJUDICATOR = "adjudicator"

#: The two archetypes whose veto is enforced in code, by id.
HARD_VETO_IDS = ("risk-officer", "brand-steward")

#: A charter CLAIMS a veto when it attributes one to its own archetype. Merely
#: mentioning that gates veto ads, or that another agent holds a veto, is not
#: a claim — the adjudicator's charter does both.
_VETO_CLAIM = re.compile(
    r"(?i)\b(your|my)\s+(hard\s+)?veto"          # "## Your hard veto", "my veto applies"
    r"|\bholds?\s+(?:a|an|the)\s+(?:\w+\s+){0,2}veto"  # "holds a hard veto"
    r"|\bveto\s+over\b"                          # "a hard veto over destinations"
)

_EMPHASIS = re.compile(r"\*\*|__|`")


def _charters(plugin_root: Path) -> list[Path]:
    return sorted((plugin_root / "agents").glob("*.md"))


def _plain_text(path: Path) -> str:
    """A charter with emphasis markers stripped, so a title match reflects
    the name a reader sees rather than the asterisks around it."""
    return _EMPHASIS.sub("", path.read_text(encoding="utf-8"))


def test_every_charter_has_name_and_description(
    plugin_root: Path, frontmatter
) -> None:
    charters = _charters(plugin_root)
    assert charters, "plugin/agents/ contains no charter files"
    for path in charters:
        meta = frontmatter(path)
        for key in ("name", "description"):
            value = meta.get(key)
            assert isinstance(value, str) and value.strip(), (
                f"{path.name}: frontmatter {key!r} must be a non-empty string"
            )


def test_charter_name_equals_filename_stem(plugin_root: Path, frontmatter) -> None:
    for path in _charters(plugin_root):
        assert frontmatter(path)["name"] == path.stem, (
            f"{path.name}: frontmatter name must equal the filename stem — "
            "Claude Code resolves agents by filename"
        )


def test_roster_ids_match_charter_files(plugin_root: Path) -> None:
    """THE DRIFT CATCHER. The charter set is exactly the roster plus the
    adjudicator — rename one without the other and this fails."""
    charter_ids = {path.stem for path in _charters(plugin_root)}
    assert ADJUDICATOR in charter_ids, (
        "the adjudicator charter is missing from plugin/agents/"
    )
    non_adjudicators = charter_ids - {ADJUDICATOR}
    roster_ids = {archetype.id for archetype in AGENT_ROSTER}
    assert non_adjudicators == roster_ids, (
        "plugin/agents/ and moot.council.AGENT_ROSTER have drifted: "
        f"charters without a roster seat {sorted(non_adjudicators - roster_ids)}, "
        f"roster seats without a charter {sorted(roster_ids - non_adjudicators)}"
    )


def test_every_chartered_archetype_documents_a_blind_spot(plugin_root: Path) -> None:
    """docs/agents.md requires every archetype to own its blind spot — an
    agent that cannot say where its mandate misleads it cannot be weighed."""
    for path in _charters(plugin_root):
        if path.stem == ADJUDICATOR:
            continue
        text = path.read_text(encoding="utf-8")
        assert re.search(r"(?im)^#{2,}[^\n]*blind spot", text) or re.search(
            r"(?i)\*\*blind spot\*\*", text
        ), f"{path.name}: no blind-spot heading or bolded phrase"


def test_roster_titles_appear_in_their_charters(plugin_root: Path) -> None:
    """council.py renders each archetype's `title` into the debate brief; the
    charter must carry the same name a reader sees there."""
    for archetype in AGENT_ROSTER:
        path = plugin_root / "agents" / f"{archetype.id}.md"
        assert path.is_file(), f"no charter file for roster id {archetype.id!r}"
        assert archetype.title.lower() in _plain_text(path).lower(), (
            f"{path.name}: roster title {archetype.title!r} appears nowhere in "
            "the charter — runtime and charter disagree on the archetype's name"
        )


def test_hard_veto_charters_name_their_veto(plugin_root: Path) -> None:
    for archetype_id in HARD_VETO_IDS:
        text = (plugin_root / "agents" / f"{archetype_id}.md").read_text(
            encoding="utf-8"
        )
        assert re.search(r"(?i)\bveto\b", text), (
            f"{archetype_id}.md: holds a hard veto in code but the charter "
            "never says so"
        )


def test_no_other_charter_claims_a_veto(plugin_root: Path) -> None:
    for path in _charters(plugin_root):
        if path.stem in HARD_VETO_IDS:
            continue
        match = _VETO_CLAIM.search(path.read_text(encoding="utf-8"))
        assert match is None, (
            f"{path.name}: claims a veto ({match.group(0)!r}) but only "
            f"{HARD_VETO_IDS} hold one — an advisory agent that claims a veto "
            "is misdescribing the mechanical layer"
        )


def test_docs_agents_covers_the_roster(repo_root: Path) -> None:
    """docs/agents.md is the roster document; it cannot drift from the code."""
    doc = (repo_root / "docs" / "agents.md").read_text(encoding="utf-8").lower()
    for archetype in AGENT_ROSTER:
        assert archetype.id in doc or archetype.title.lower() in doc, (
            f"docs/agents.md mentions neither {archetype.id!r} nor "
            f"{archetype.title!r}"
        )
    assert ADJUDICATOR in doc, "docs/agents.md does not cover the adjudicator"
