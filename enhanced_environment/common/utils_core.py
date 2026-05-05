"""Legacy utilities aggregator for backward compatibility."""

# [AGENT-EDIT] utils_core.py now re-exports split utility modules.

from datetime import datetime

from enhanced_environment.common.settings import (
    VERBOSE_CONVERSION,
    DEBUG_MODE,
    QUIET_MODE,
)
from enhanced_environment.common.violation_utils import (
    _SEVERITY_PRIORITY,
    RELAX_META_IDS,
    RELAX_EVENT_IDS,
    META_ID_PREFIXES,
    normalize_constraint_family,
    build_violation_event_key,
    split_violations,
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


def count_expanded_block_units(blocks) -> int:
    """[AGENT-ADD] 결과 row 기준 블록 수를 계산한다."""
    total = 0
    for block in blocks or []:
        originals = getattr(block, "subassembly_original_blocks", None) or [block]
        total += max(1, len(originals))
    return total


_SCHEDULE_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


def parse_schedule_timestamp(raw_value):
    """[AGENT-ADD] 결과 row의 시간 문자열을 datetime으로 파싱한다."""
    if raw_value is None:
        return None
    if isinstance(raw_value, datetime):
        return raw_value

    text = str(raw_value).strip()
    if not text or text.lower() == "nan":
        return None

    for fmt in _SCHEDULE_TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def compute_schedule_span_hours(results) -> float:
    """[AGENT-ADD] 첫 panel 시작부터 마지막 final 종료까지의 wall-clock makespan."""
    start_times = []
    end_times = []

    for result in results or []:
        start_dt = parse_schedule_timestamp(
            result.get("panel_start_time") or result.get("start_time")
        )
        end_dt = parse_schedule_timestamp(
            result.get("final_end_time") or result.get("end_time")
        )
        if start_dt is not None:
            start_times.append(start_dt)
        if end_dt is not None:
            end_times.append(end_dt)

    if not start_times or not end_times:
        return 0.0

    return (max(end_times) - min(start_times)).total_seconds() / 3600.0


def compute_sum_daily_max_makespan_hours(results) -> float:
    """[AGENT-ADD] 날짜별 최대 row makespan 합산값 (보조 진단용)."""
    makespan_by_date = {}
    for result in results or []:
        date_key = str(result.get("date") or "")
        if not date_key:
            continue
        row_makespan = float(result.get("makespan_hours", 0) or 0)
        if date_key not in makespan_by_date or row_makespan > makespan_by_date[date_key]:
            makespan_by_date[date_key] = row_makespan
    return float(sum(makespan_by_date.values()))


def sum_result_violation_counts(results, field_name: str, fallback_field: str = None) -> int:
    """[AGENT-ADD] 결과 row에서 누적 위반 카운트를 합산한다."""
    total = 0
    for result in results or []:
        if field_name in result:
            value = result.get(field_name, 0)
        elif fallback_field:
            value = result.get(fallback_field, 0)
        else:
            value = 0
        total += int(value or 0)
    return total


def count_primary_cseam_violations(results) -> int:
    """[AGENT-ADD] 결과 row에서 C/Seam family의 ERROR/WARNING 위반만 센다."""
    cseam_ids = {"ROUTING_C_SEAM_SPACING", "C_SEAM_SPACING"}
    total = 0
    for result in results or []:
        constraint_ids = result.get("constraint_ids", []) or []
        severities = result.get("violation_severity", []) or []
        for idx, constraint_id in enumerate(constraint_ids):
            if constraint_id not in cseam_ids:
                continue
            severity = str(severities[idx] if idx < len(severities) else "INFO").upper()
            if severity in {"ERROR", "WARNING"}:
                total += 1
    return total

__all__ = [
    "VERBOSE_CONVERSION",
    "DEBUG_MODE",
    "QUIET_MODE",
    "_SEVERITY_PRIORITY",
    "RELAX_META_IDS",
    "RELAX_EVENT_IDS",
    "META_ID_PREFIXES",
    "normalize_constraint_family",
    "build_violation_event_key",
    "split_violations",
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
    "count_expanded_block_units",
    "parse_schedule_timestamp",
    "compute_schedule_span_hours",
    "compute_sum_daily_max_makespan_hours",
    "sum_result_violation_counts",
    "count_primary_cseam_violations",
    "resolve_line_group_for_block",
    "get_line_group_and_workshop_code",
]
