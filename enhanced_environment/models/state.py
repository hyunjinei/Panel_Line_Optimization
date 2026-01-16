# [AGENT-ADD] Split from data_structures.py to improve readability.
"""Environment and sequence state data classes."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple

from .enums import BayType, AssemblyType, ProcessPhase

# Type aliases (kept for backward compatibility)
BlockSequence = List[int]
BayAssignment = Dict[int, BayType]
ActionMask = List[bool]
CapacityStatus = Tuple[int, int, int, int]


@dataclass
class EnvironmentState:
    """Environment state snapshot (capacity, bay, P/S, etc.)."""
    current_time: datetime
    current_date: datetime
    is_weekend: bool = False
    is_holiday: bool = False
    is_hot_season: bool = False

    # Capacity (seam-based)
    daily_seam_used: int = 0
    weekend_seam_used: int = 0

    # Bay state
    bay_35a_worktime: float = 0.0
    bay_36b_worktime: float = 0.0
    last_bay_assignment: Optional[BayType] = None
    bay_35a_consecutive_count: int = 0
    bay_36b_main_plate_consecutive: int = 0
    block_10_b_bay_consecutive: int = 0

    # P/S tracking
    pending_p_blocks: Dict[int, int] = field(default_factory=dict)
    completed_s_blocks: List[int] = field(default_factory=list)

    # Mixing tracking
    last_assembly_type: Optional[AssemblyType] = None
    cross_seam_consecutive_count: int = 0

    # Special counters
    line_b_fab_3seam_gap_counter: int = 0

    # Completed blocks
    completed_blocks: List[int] = field(default_factory=list)

    def get_daily_capacity_limit(self) -> int:
        """Daily seam capacity limit."""
        if self.is_weekend:
            return 45
        base_seam = 75
        if self.is_hot_season:
            base_seam -= 1.5
        return int(base_seam)

    def get_daily_capacity_used(self) -> int:
        """Current seam usage."""
        return self.weekend_seam_used if self.is_weekend else self.daily_seam_used

    def can_add_capacity(self, seam_count: int) -> bool:
        """Check if seam capacity can accept new load."""
        return self.get_daily_capacity_used() + seam_count <= self.get_daily_capacity_limit()


@dataclass
class SequenceState:
    """PFSP sequence and step-by-step state."""
    block_sequence: List[int] = field(default_factory=list)
    sequence_decided: bool = False

    selected_blocks: List[int] = field(default_factory=list)
    last_assembly_type: Optional[AssemblyType] = None

    current_block_index: int = 0
    current_process: int = 1
    current_phase: ProcessPhase = ProcessPhase.SEQUENCE_DECISION

    common_processes: List[int] = field(default_factory=lambda: [1, 2, 3, 4, 5])
    branch_processes: List[int] = field(default_factory=lambda: [6, 7, 8])

    branch_assignments: Dict[int, BayType] = field(default_factory=dict)

    def advance_to_next_step(self) -> None:
        """Move to next process or block."""
        if self.current_process < 8:
            self.current_process += 1
        else:
            self.current_block_index += 1
            self.current_process = 1

    def get_next_block_to_process(self) -> Optional[int]:
        """Return next block id to process."""
        if self.sequence_decided and self.current_block_index < len(self.block_sequence):
            return self.block_sequence[self.current_block_index]
        return None

    def is_branch_point(self) -> bool:
        """True if current process is the branch point."""
        return self.current_process == 6

    def is_completed(self) -> bool:
        """True if all steps are done."""
        return self.sequence_decided and self.current_block_index >= len(self.block_sequence)

    def get_progress_ratio(self) -> float:
        """Progress ratio in [0, 1]."""
        if not self.sequence_decided:
            if not self.block_sequence:
                return 0.0
            return len(self.selected_blocks) / len(self.block_sequence) * 0.1

        if not self.block_sequence:
            return 0.0

        total_steps = len(self.block_sequence) * 8
        completed_steps = self.current_block_index * 8 + (self.current_process - 1)
        progress = 0.1 + (completed_steps / total_steps) * 0.9
        return min(progress, 1.0)

    def get_progress_info(self) -> Dict[str, Any]:
        """Progress info dict for logging/debugging."""
        current_block_id = self.get_next_block_to_process()
        progress_ratio = self.get_progress_ratio()

        return {
            "progress_percentage": progress_ratio * 100.0,
            "current_process": self.current_process,
            "current_block_id": current_block_id,
            "current_block_index": self.current_block_index,
            "total_blocks": len(self.block_sequence) if self.sequence_decided else 0,
            "sequence_decided": self.sequence_decided,
            "is_completed": self.is_completed(),
            "phase": self.current_phase.value,
            "is_branch_point": self.is_branch_point(),
            "selected_blocks_count": len(self.selected_blocks),
            "last_assembly_type": self.last_assembly_type.value if self.last_assembly_type else None,
            "sequence_selection_progress": (
                f"{len(self.selected_blocks)}/{len(self.block_sequence) if self.block_sequence else '?'}"
            ),
        }
