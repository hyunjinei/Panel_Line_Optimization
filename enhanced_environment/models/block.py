# [AGENT-ADD] Split from data_structures.py to improve readability.
"""EnhancedBlock definition (panel block domain model)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Tuple

from .enums import (
    AssemblyType,
    PortStarboard,
    WorkshopType,
    BayType,
    MaterialType,
)


@dataclass
class EnhancedBlock:
    """
    Extended block information used by constraints and scheduling.

    Notes:
    - Keep fields and defaults identical to the legacy data_structures.py to
      avoid behavior changes.
    """
    # Base info
    block_id: int
    processing_times: List[float]

    # P5#1: due-date based start limit
    max_start_date: datetime

    # P5#3, P5#4: P/S pairing
    assembly_type: AssemblyType = AssemblyType.LINE
    port_starboard: PortStarboard = PortStarboard.NONE
    pair_block_id: Optional[int] = None
    assembly_start_date: datetime = field(default_factory=datetime.now)

    # P5#6: FAB flag
    is_fab: bool = False

    # Additional attributes
    assembly_workshop_code: str = ""
    line_group: Optional[str] = None
    curved_plate_count: int = 0
    has_curved_plate: bool = False
    is_high_seam_block: bool = False

    # P5#8,9,10: capacity
    seam_count: int = 1
    c_seam_count: int = 0

    # P5#11,12: mixing
    workshop_type: WorkshopType = WorkshopType.NONE

    # P5#13: material availability
    material_ready: bool = True

    # P5#17: physical characteristics
    length: Optional[float] = None
    min_thickness: Optional[float] = None
    max_thickness: Optional[float] = None

    # [AGENT-EDIT] P6#1,2,3 metadata only: 시간 제약은 논문 실험 기준 제거
    is_draft: bool = False
    is_cross_seam: bool = False
    main_plate_count: int = 0

    # [AGENT-EDIT] P6#4는 pure cross seam 혼합 규칙으로 재정의
    requires_mixed_placement: bool = False

    # P7#2: width
    width: float = 15.0

    # P7#3,4,10: longi count
    longi_count: int = 10

    # P7#8: main plate only
    is_main_plate_only: bool = False

    # P7#9: angle/build-up
    angle_count: int = 0
    buildup_count: int = 0

    # P7#11: block number
    block_number: int = 1

    # P7#12: material type
    material_type: MaterialType = MaterialType.NORMAL

    # Bay assignment (result)
    assigned_bay: BayType = BayType.AUTO

    # Metadata
    creation_time: datetime = field(default_factory=datetime.now)
    priority: int = 0

    def __post_init__(self) -> None:
        """Post-init validation (kept identical)."""
        if len(self.processing_times) != 8:
            raise ValueError(f"처리시간은 8개 공정이어야 함: {len(self.processing_times)}")

    def is_p_s_pair(self) -> bool:
        """Check if this block is a P/S pair."""
        return self.port_starboard != PortStarboard.NONE and self.pair_block_id is not None

    def needs_afternoon_start(self) -> bool:
        """[AGENT-EDIT] P6#1,2,3 시간 제약 제거: legacy 호출 호환용으로 항상 False."""
        return False

    def get_bay_constraint(self) -> BayType:
        """Physical constraints are relaxed; keep AUTO."""
        return BayType.AUTO

    def get_physical_characteristics_key(self) -> tuple:
        """Key for P5#17 physical identity."""
        return (
            round(self.width, 1) if self.width else 0,
            self.seam_count,
            self.is_cross_seam,
            self.longi_count,
            round(self.length, 1) if self.length else 0,
            round(self.min_thickness, 1) if self.min_thickness else 0,
            round(self.max_thickness, 1) if self.max_thickness else 0,
        )

    def is_physically_identical_to(self, other: "EnhancedBlock") -> bool:
        """P5#17: physical identity check."""
        return self.get_physical_characteristics_key() == other.get_physical_characteristics_key()

    def is_ps_small_pair(self, blocks_dict: Optional[Dict[int, "EnhancedBlock"]] = None) -> bool:
        """
        P/S small pair check (both longi < 7).
        """
        if not self.is_p_s_pair() or not self.pair_block_id:
            return False

        if blocks_dict and self.pair_block_id in blocks_dict:
            pair_block = blocks_dict[self.pair_block_id]
            return (self.longi_count < 7 and pair_block.longi_count < 7)
        return self.longi_count < 7

    def will_force_bay_b(self, blocks_dict: Optional[Dict[int, "EnhancedBlock"]] = None) -> bool:
        """
        Check if this block forces Bay 36B (for 3-bay prevention).
        """
        if self.width > 21.0:
            return True
        if self.is_ps_small_pair(blocks_dict):
            return True
        return False
