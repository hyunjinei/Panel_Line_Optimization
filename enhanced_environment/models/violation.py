# [AGENT-ADD] Split from data_structures.py to improve readability.
"""Constraint violation data class."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ConstraintViolation:
    """Constraint violation record."""
    constraint_id: str
    message: str
    severity: str = "ERROR"
    block_id: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def __str__(self) -> str:
        return f"[{self.severity}] {self.constraint_id}: {self.message}"
