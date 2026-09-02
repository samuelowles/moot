"""Cross-cutting drift catchers — the seams between config, code and docs.

Each of these guards a failure that has actually shipped somewhere: an
authorized verb with no executor silently fails at dispatch; a §N citation
that points at a section a doc restructure deleted; a README badge that
advertises a test count the suite no longer has.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from moot.writes import ALLOWED_VERBS

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: src modules whose §N citations are validated, one parameter each so a
#: dangling pointer names its file.
_SRC_FILES = sorted((_REPO_ROOT / "src" / "moot").rglob("*.py"))

#: A citation names its document ("<doc>.md §N") or defaults to gates.md.
_DOC_CITATION = re.compile(r"([a-z-]+\.md)\s*§(\d+(?:\.\d+)?)")
_BARE_CITATION = re.compile(r"§(\d+(?:\.\d+)?)")

_DOCS = {
    name: _REPO_ROOT / "docs" / name
    for name in ("gates.md", "framework.md", "writes.md", "debate-protocol.md",
                 "agents.md", "scheduling.md")
}

_LEVEL_TWO = re.compile(r"^##\s+(\d+)\.\s")


def test_every_authorized_verb_has_an_executor(example_config: Path) -> None:
    """An envelope.authorized verb with no executor in the write layer is a
    silent failure at dispatch — the run believes it may act and cannot."""
    config = yaml.safe_load(example_config.read_text(encoding="utf-8"))
    authorized = list((config.get("envelope") or {}).get("authorized") or [])
    assert authorized, "the example config authorizes no verbs at all"
    missing = [verb for verb in authorized if verb not in ALLOWED_VERBS]
    assert not missing, (
        f"envelope.authorized names verbs moot.writes.ALLOWED_VERBS cannot "
        f"execute: {missing}"
    )


def test_no_forbidden_verb_is_executable(example_config: Path) -> None:
    config = yaml.safe_load(example_config.read_text(encoding="utf-8"))
    forbidden = list((config.get("envelope") or {}).get("forbidden") or [])
    assert forbidden, "the example config forbids no verbs at all"
    executable = sorted(set(forbidden) & set(ALLOWED_VERBS))
    assert not executable, (
        f"envelope.forbidden names verbs the write layer WILL execute: "
        f"{executable} — forbidden must never intersect the allowlist"
    )


def _doc_numbers(path: Path) -> set[str]:
    """Section numbers that exist in one document: ``## N.`` / ``### N.M``
    headings, plus numbered list items inside a section (gates.md §11 is a
    numbered list, not subsections). Fenced code blocks are skipped — §6's
    numbered conditions live in one and are conditions, not sections."""
    numbers: set[str] = set()
    section: str | None = None
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = re.match(r"^#{2,3}\s+(\d+(?:\.\d+)?)\.?", line)
        if heading:
            numbers.add(heading.group(1))
            section = heading.group(1).split(".")[0]
            continue
        if re.match(r"^#{2,3}\s+", line):
            section = None
            continue
        item = re.match(r"^(\d+)\.\s", line)
        if item and section:
            numbers.add(f"{section}.{item.group(1)}")
    return numbers


@pytest.fixture(scope="module")
def valid_numbers() -> dict[str, set[str]]:
    return {name: _doc_numbers(path) for name, path in _DOCS.items()}


_SRC_IDS = [str(p.relative_to(_REPO_ROOT)) for p in _SRC_FILES]


@pytest.mark.parametrize("source", _SRC_FILES, ids=_SRC_IDS)
def test_section_citations_resolve(source: Path, valid_numbers) -> None:
    """Every §N citation in src must point at a section its document has.
    Named-document citations validate against that document; bare §N and
    "gates.md §N" validate against gates.md."""
    for lineno, line in enumerate(
        source.read_text(encoding="utf-8").splitlines(), start=1
    ):
        for match in _DOC_CITATION.finditer(line):
            doc, number = match.group(1), match.group(2)
            assert doc in valid_numbers, (
                f"{source.name}:{lineno} cites unknown document {doc!r}"
            )
            assert number in valid_numbers[doc], (
                f"{source.name}:{lineno} cites {doc} §{number}, which has no "
                "such section"
            )
        remainder = _DOC_CITATION.sub("", line)
        for match in _BARE_CITATION.finditer(remainder):
            assert match.group(1) in valid_numbers["gates.md"], (
                f"{source.name}:{lineno} cites gates.md §{match.group(1)}, "
                "which has no such section"
            )


def _gates_level_two_numbers() -> list[int]:
    numbers = []
    for line in (_REPO_ROOT / "docs" / "gates.md").read_text(
        encoding="utf-8"
    ).splitlines():
        match = _LEVEL_TWO.match(line)
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def test_gates_section_numbers_are_contiguous() -> None:
    """A deleted gates.md section must not leave a hole every citation can
    then fall into."""
    numbers = _gates_level_two_numbers()
    assert numbers, "gates.md has no numbered ## sections"
    expected = set(range(1, max(numbers) + 1))
    missing = expected - set(numbers)
    assert not missing, (
        f"gates.md ## sections are not contiguous — missing {sorted(missing)}"
    )


def test_gates_section_numbers_are_unique() -> None:
    numbers = _gates_level_two_numbers()
    assert len(numbers) == len(set(numbers)), (
        f"gates.md has duplicate ## section numbers: "
        f"{sorted(n for n in numbers if numbers.count(n) > 1)}"
    )


_BADGE = re.compile(r"badge/tests-(\d+)%20passing")


def test_readme_test_count_badge_is_current(repo_root: Path) -> None:
    """The badge is the first thing a visitor reads; a stale count claims a
    verification level the suite no longer has."""
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    match = _BADGE.search(readme)
    if match is None:
        pytest.skip("README.md carries no test-count badge")
    badge_count = int(match.group(1))
    collected = _collected_test_count(repo_root)
    tolerance = max(1, round(collected * 0.10))
    assert abs(badge_count - collected) <= tolerance, (
        f"README badge says {badge_count} tests; the suite collects "
        f"{collected} (tolerance ±{tolerance})"
    )


def _collected_test_count(repo_root: Path) -> int:
    """Collect the suite in a subprocess — the honest count, e2e included.
    pytest 9's ``-q --collect-only`` prints per-file counts without a total,
    so those are summed; older output carried an explicit total line."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=300,
    )
    assert result.returncode == 0, f"collection failed: {result.stderr[-500:]}"
    per_file = re.findall(r"^\S+:\s*(\d+)$", result.stdout, flags=re.MULTILINE)
    if per_file:
        return sum(int(count) for count in per_file)
    match = re.search(r"(\d+) tests? collected", result.stdout)
    assert match, f"could not parse a collected count from: {result.stdout[-200:]}"
    return int(match.group(1))
