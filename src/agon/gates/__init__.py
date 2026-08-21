"""Gate families — the executable form of docs/gates.md §4–§8.

Each module implements the section named in its docstring against the
:class:`agon.gates.base.Gate` protocol. Precedence between their results is
resolved by the pipeline per §12, never inside a gate.
"""

from agon.gates.base import Gate, GateContext, auction_check
from agon.gates.budget import BUDGET_STEP_HARD_CAP_PCT, BudgetGate, clamp_step_pct
from agon.gates.demote import DemoteGate
from agon.gates.fatigue import FatigueGate, WatchGate
from agon.gates.graduate import GraduateGate
from agon.gates.kill import KillGate

__all__ = [
    "BUDGET_STEP_HARD_CAP_PCT",
    "BudgetGate",
    "DemoteGate",
    "FatigueGate",
    "Gate",
    "GateContext",
    "GraduateGate",
    "KillGate",
    "WatchGate",
    "auction_check",
    "clamp_step_pct",
]
