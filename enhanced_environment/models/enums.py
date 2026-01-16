# [AGENT-ADD] Split from data_structures.py to improve readability.
"""Enum definitions used across the scheduling environment."""

from enum import Enum


class AssemblyType(Enum):
    """Assembly type."""
    LINE = "line"
    FIXED = "fixed"
    EXTERNAL_M = "external_m"
    INTERNAL_A = "internal_a"


class PortStarboard(Enum):
    """Port/Starboard type."""
    PORT = "P"
    STARBOARD = "S"
    CENTER = "C"
    NONE = "NONE"


class WorkshopType(Enum):
    """Workshop type."""
    WORKSHOP_A = "A"
    WORKSHOP_B = "B"
    NONE = "NONE"


class BayType(Enum):
    """Bay type."""
    AUTO = "AUTO"
    BAY_35A = "35A"
    BAY_36B = "36B"
    COMMON = "COMMON"


class MaterialType(Enum):
    """Material type."""
    NORMAL = "NORMAL"
    LT = "LT"
    SPECIAL = "SPECIAL"


class ProcessPhase(Enum):
    """PFSP process phase."""
    SEQUENCE_DECISION = "SEQUENCE"
    PROCESS_EXECUTION = "PROCESS"
    BRANCH_SELECTION = "BRANCH"
    COMPLETED = "COMPLETED"
