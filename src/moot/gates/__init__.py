"""Gate families — the executable form of docs/gates.md §4–§8.

Each module implements the section named in its docstring. Precedence
between their results is resolved by the pipeline per §12, never inside a
gate.
"""

from moot.gates.base import GateContext, auction_check
from moot.gates.budget import BUDGET_STEP_HARD_CAP_PCT, BudgetGate, clamp_step_pct
from moot.gates.demote import DemoteGate
from moot.gates.fatigue import FatigueGate, WatchGate
from moot.gates.graduate import GraduateGate
from moot.gates.kill import KillGate

__all__ = [
    "BUDGET_STEP_HARD_CAP_PCT",
    "BudgetGate",
    "DemoteGate",
    "FatigueGate",
    "GateContext",
    "GraduateGate",
    "KillGate",
    "WatchGate",
    "auction_check",
    "clamp_step_pct",
]
