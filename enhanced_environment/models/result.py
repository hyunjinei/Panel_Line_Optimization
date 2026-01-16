# [AGENT-ADD] Split from data_structures.py to improve readability.
"""Action result data class."""

from dataclasses import dataclass, field
from typing import List, Dict, Any

from .enums import BayType
from .violation import ConstraintViolation


@dataclass
class ActionResult:
    """Result for a single action/decision."""
    success: bool
    block_id: int
    assigned_bay: BayType
    processing_time: float
    constraint_violations: List[ConstraintViolation] = field(default_factory=list)
    state_changes: Dict[str, Any] = field(default_factory=dict)
    makespan_delta: float = 0.0

    def add_violation(self, constraint_id: str, message: str, severity: str = "ERROR") -> None:
        """Append a constraint violation."""
        violation = ConstraintViolation(
            constraint_id=constraint_id,
            message=message,
            severity=severity,
            block_id=self.block_id,
        )
        self.constraint_violations.append(violation)
