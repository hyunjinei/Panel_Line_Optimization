"""Legacy utilities aggregator for backward compatibility."""

# [AGENT-EDIT] utils_core.py now re-exports split utility modules.

from enhanced_environment.common.settings import (
    VERBOSE_CONVERSION,
    DEBUG_MODE,
    QUIET_MODE,
)
from enhanced_environment.common.violation_utils import (
    _SEVERITY_PRIORITY,
    RELAX_META_IDS,
    RELAX_EVENT_IDS,
    extract_relax_constraints,
    count_relax_events,
    summarize_violations,
    dedup_violations,
)
from enhanced_environment.common.time_utils import TimeUtils
from enhanced_environment.common.data_converter import DataConverter
from enhanced_environment.common.logger_utils import Logger
from enhanced_environment.common.validation_utils import ValidationUtils
from enhanced_environment.common.performance_analyzer import PerformanceAnalyzer

# [AGENT-EDIT] DataConverter static helpers exposed for backward compatibility
expand_rows_with_subassembly = DataConverter.expand_rows_with_subassembly
resolve_line_group_for_block = DataConverter.resolve_line_group_for_block
get_line_group_and_workshop_code = DataConverter.get_line_group_and_workshop_code

__all__ = [
    "VERBOSE_CONVERSION",
    "DEBUG_MODE",
    "QUIET_MODE",
    "_SEVERITY_PRIORITY",
    "RELAX_META_IDS",
    "RELAX_EVENT_IDS",
    "extract_relax_constraints",
    "count_relax_events",
    "summarize_violations",
    "dedup_violations",
    "TimeUtils",
    "DataConverter",
    "Logger",
    "ValidationUtils",
    "PerformanceAnalyzer",
    "expand_rows_with_subassembly",
    "resolve_line_group_for_block",
    "get_line_group_and_workshop_code",
]
