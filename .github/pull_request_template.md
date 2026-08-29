## What this changes

One or two sentences. If this is a gate change, name the section of
`docs/gates.md` it implements or revises.

## The three-edits rule (gate changes only)

CONTRIBUTING.md: "A gate change is three edits, and all three are required."
If this PR touches gate behaviour, confirm all three — a PR missing any one
of them cannot merge:

- [ ] **`docs/gates.md`** updated — the arithmetic, with the reasoning
- [ ] **Implementation** updated, citing the section it implements
- [ ] **Test added** that fails without the change

(For non-gate changes, delete this section.)

## Verification

Paste the commands you ran and their outcome. The CI trio is mandatory:

```
ruff check src tests
mypy src
python -m pytest tests/ -q
```

## Safety check

- [ ] No delete verb and no HTTP DELETE introduced anywhere
- [ ] No new runtime dependency beyond pyyaml, requests and click
- [ ] Behaviour of existing gates unchanged, or the threshold move is
      justified below and documented in `docs/gates.md`
- [ ] No network calls in tests
