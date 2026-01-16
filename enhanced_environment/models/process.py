# [AGENT-ADD] Split from data_structures.py to improve readability.
"""Process step data class."""

from dataclasses import dataclass
from datetime import datetime

from .enums import BayType


@dataclass
class ProcessStep:
    """Single process step (for makespan reconstruction)."""
    block_id: int
    process_num: int
    bay_type: BayType
    start_time: datetime
    end_time: datetime
    processing_time: float

    completion_time: float
    predecessor_job_ct: float = 0.0
    predecessor_process_ct: float = 0.0

    def get_earliest_start_time(self) -> float:
        """Earliest start time computed from predecessors."""
        return max(self.predecessor_job_ct, self.predecessor_process_ct)
