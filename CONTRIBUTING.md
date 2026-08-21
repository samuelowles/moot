# Contributing

Issues and pull requests welcome. A few things specific to this project.

## Security first

If a change could cause an unauthorised write, read [SECURITY.md](SECURITY.md)
before opening anything public. Bugs in gate arithmetic are ordinary issues;
bypasses of the write safety layer are not.

## Setup

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
ruff check src tests
```

Tests use the fixture adapter and make no network calls. They need no
credentials, and a test that requires either will not be merged.

## Changing a gate

The gates are specified in [`docs/gates.md`](docs/gates.md) and implemented in
`src/agon/gates/`. A gate change is three edits, and all three are required:

1. **`docs/gates.md`** — the arithmetic, with the reasoning.
2. **The implementation**, citing the section it implements.
3. **A test** that fails without the change.

Add an ADR under `docs/adr/` when the change is hard to reverse or encodes a
judgement someone will later want to argue with. The existing ADRs are the
model: state the defect with real numbers, show the alternative you rejected
and why, and give revert conditions.

Do not tune a threshold without saying what evidence moved it. The defaults are
fitted to one account's economics and are meant to be argued with — but from
data, not from taste.

## Changing the write path

`src/agon/writes.py` and `src/agon/guards.py` are load-bearing. Moving a check
out of either — into a prompt, a charter, or a caller's responsibility — is a
security change even when it reads as a refactor. Say so in the PR description.

Two rules that are not negotiable: **no delete verb** anywhere in the codebase,
and **nothing may route around `AGON_READ_ONLY`**. Both are enforced by tests.

## Changing an agent charter

Charters live in `plugin/agents/` and are summarised in
[`docs/agents.md`](docs/agents.md). Keep the two in step.

The agents are meant to be *partial*. A pull request that makes an archetype
more balanced is almost always making it worse — the balance is supposed to
emerge from the collision, not from any single charter. Every archetype needs a
documented blind spot and at least two natural opponents.

## Style

Type hints throughout. Module docstrings explain *why* and cite the
`docs/gates.md` section they implement. No bare excepts. `logging`, not
`print`, outside the CLI and reporter.
