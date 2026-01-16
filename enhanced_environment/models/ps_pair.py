# [AGENT-ADD] Split from data_structures.py to improve readability.
"""P/S pair data class."""

from dataclasses import dataclass
from datetime import datetime

from .enums import AssemblyType


@dataclass
class PSBlockPair:
    """P/S block pair info."""
    port_block_id: int
    starboard_block_id: int
    assembly_type: AssemblyType
    assembly_start_date: datetime
    longi_count_port: int
    longi_count_starboard: int

    def needs_same_bay(self) -> bool:
        """P7#3,4: same bay required for small pairs."""
        return (self.longi_count_port < 7 and self.longi_count_starboard < 7)

    def is_continuous_required(self) -> bool:
        """Check if continuous placement is required."""
        if self.assembly_type == AssemblyType.LINE:
            return True
        if self.assembly_type == AssemblyType.FIXED:
            return abs((self.assembly_start_date - self.assembly_start_date).days) <= 1
        return False
