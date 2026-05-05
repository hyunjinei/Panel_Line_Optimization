"""Runtime configuration loader for main.py orchestration."""

# [AGENT-ADD] Central runtime config shared across modules.

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Iterable

_RUNTIME_CONFIG: Dict[str, Any] = {}

# [AGENT-ADD] Bias components are candidate-reduction toggles, not hard constraints.
BIAS_COMPONENT_FIELD_MAP: Dict[int, str] = {
    1: "enable_workshop_head_masking",
    2: "enable_assembly_start_leadtime_layers",
    3: "enable_assembly_start_window_filter",
}


def set_runtime_config(config: Optional[Dict[str, Any]]) -> None:
    """Set runtime configuration for the current process."""
    global _RUNTIME_CONFIG
    _RUNTIME_CONFIG = config or {}


def get_runtime_config() -> Dict[str, Any]:
    """Return runtime configuration (empty dict if not set)."""
    return _RUNTIME_CONFIG or {}


def parse_bias_components(raw_value: Any) -> Optional[list[int]]:
    """Parse bias component selector from config/CLI.

    Supported forms:
    - None -> None
    - [] / [1, 3]
    - "1,3"
    - "{1,3}"
    - "1.3" / "{1.3}"
    """
    if raw_value is None:
        return None

    tokens: list[Any]
    if isinstance(raw_value, (list, tuple, set)):
        tokens = list(raw_value)
    elif isinstance(raw_value, int):
        tokens = [raw_value]
    else:
        text = str(raw_value).strip()
        if not text:
            return []
        text = text.translate(str.maketrans({
            "{": " ",
            "}": " ",
            "[": " ",
            "]": " ",
            "(": " ",
            ")": " ",
            ";": ",",
            "/": ",",
            ".": ",",
        }))
        tokens = [part for part in re.split(r"[\s,]+", text) if part]

    parsed: list[int] = []
    for token in tokens:
        try:
            value = int(str(token).strip())
        except Exception as exc:
            raise ValueError(f"bias_components에 숫자가 아닌 값이 포함됨: {token}") from exc
        if value not in BIAS_COMPONENT_FIELD_MAP:
            raise ValueError(f"bias_components는 1,2,3만 허용됨: {value}")
        parsed.append(value)
    return sorted(set(parsed))


def apply_bias_components_to_constraints(constraints: Dict[str, Any], raw_value: Any) -> Dict[str, Any]:
    """Apply parsed bias component selector to the three bias flags."""
    components = parse_bias_components(raw_value)
    if components is None:
        return dict(constraints)

    updated = dict(constraints)
    enabled = set(components)
    for component_id, field_name in BIAS_COMPONENT_FIELD_MAP.items():
        updated[field_name] = component_id in enabled
    updated["bias_components"] = list(components)
    return updated


def describe_bias_components(constraints: Dict[str, Any]) -> str:
    """Return human-readable bias selector like 'off', '1,3', or '1,2,3'."""
    enabled = [
        str(component_id)
        for component_id, field_name in BIAS_COMPONENT_FIELD_MAP.items()
        if bool((constraints or {}).get(field_name, False))
    ]
    return ",".join(enabled) if enabled else "off"


def load_runtime_config(path: str) -> Dict[str, Any]:
    """Load config from yaml or json."""
    if not path:
        return {}
    if path.lower().endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
        except Exception as exc:
            raise RuntimeError("PyYAML이 필요합니다. JSON을 쓰거나 PyYAML을 설치하세요.") from exc
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    raise ValueError(f"지원하지 않는 설정 파일 형식: {path}")


def _parse_time_range(range_text: str) -> Optional[Dict[str, str]]:
    if not range_text:
        return None
    if "-" in range_text:
        start, end = [part.strip() for part in range_text.split("-", 1)]
    elif "~" in range_text:
        start, end = [part.strip() for part in range_text.split("~", 1)]
    else:
        return None
    if not start or not end:
        return None
    return {"start": start, "end": end}


def _parse_date(value: Any):
    from datetime import datetime, date
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdigit() and len(cleaned) == 8:
            return datetime.strptime(cleaned, "%Y%m%d").date()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(cleaned, fmt).date()
            except ValueError:
                continue
    return None


def _expand_date_items(items: Iterable[Any]) -> list:
    from datetime import timedelta
    expanded = []
    for raw in items or []:
        if isinstance(raw, str) and ("~" in raw or ".." in raw):
            sep = "~" if "~" in raw else ".."
            parts = [p.strip() for p in raw.split(sep, 1)]
            if len(parts) != 2:
                continue
            start = _parse_date(parts[0])
            end = _parse_date(parts[1])
            if not start or not end:
                continue
            if start > end:
                start, end = end, start
            current = start
            while current <= end:
                expanded.append(current)
                current += timedelta(days=1)
            continue
        parsed = _parse_date(raw)
        if parsed:
            expanded.append(parsed)
    return expanded


def _expand_date_map(raw_map: Any) -> Dict[Any, Any]:
    """날짜 키가 범위인 dict를 개별 날짜로 확장."""
    if not isinstance(raw_map, dict):
        return {}
    expanded: Dict[Any, Any] = {}
    for raw_key, raw_value in raw_map.items():
        if isinstance(raw_key, str) and ("~" in raw_key or ".." in raw_key):
            sep = "~" if "~" in raw_key else ".."
            parts = [p.strip() for p in raw_key.split(sep, 1)]
            if len(parts) != 2:
                continue
            start = _parse_date(parts[0])
            end = _parse_date(parts[1])
            if not start or not end:
                continue
            if start > end:
                start, end = end, start
            current = start
            from datetime import timedelta
            while current <= end:
                expanded[current] = raw_value
                current += timedelta(days=1)
        else:
            parsed = _parse_date(raw_key)
            expanded[parsed or raw_key] = raw_value
    return expanded


def build_calendar_overrides(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build CalendarManager overrides from runtime config."""
    config = config or get_runtime_config()
    calendar_cfg = (config or {}).get("calendar", {}) or {}

    overrides: Dict[str, Any] = {}

    # [AGENT-ADD] Explicit work dates override GUI-defined closed/partial-shutdown days.
    work_dates = _expand_date_items(calendar_cfg.get("work_dates") or calendar_cfg.get("open_dates") or [])
    if work_dates:
        overrides["work_dates"] = work_dates

    # [AGENT-EDIT] 휴무/반일/점심 설정은 enable 플래그를 우선 적용
    enable_closed = calendar_cfg.get("enable_holidays_off")
    if enable_closed is not False:
        closed_dates = _expand_date_items(calendar_cfg.get("holidays_off") or [])
        if closed_dates or enable_closed is True:
            overrides["enable_closed_dates"] = bool(closed_dates or enable_closed)
            if closed_dates:
                overrides["closed_dates"] = closed_dates

    enable_half_day = calendar_cfg.get("enable_half_day_off")
    enable_afternoon = calendar_cfg.get("enable_afternoon_shutdown")
    if enable_half_day is not False or enable_afternoon is not False:
        half_day = _expand_date_items(calendar_cfg.get("half_day_off") or [])
        afternoon_dates = _expand_date_items(calendar_cfg.get("afternoon_shutdown_dates") or [])
        if half_day or afternoon_dates or enable_half_day is True or enable_afternoon is True:
            overrides["enable_afternoon_shutdown"] = bool(half_day or afternoon_dates or enable_half_day or enable_afternoon)
            overrides["afternoon_shutdown_dates"] = list({*half_day, *afternoon_dates}) if (half_day or afternoon_dates) else []
            range_text = calendar_cfg.get("afternoon_shutdown", "15:00-08:00")
            parsed = _parse_time_range(range_text) or {}
            if parsed.get("start"):
                overrides["afternoon_shutdown_start"] = parsed["start"]
            if parsed.get("end"):
                overrides["afternoon_shutdown_end"] = parsed["end"]
            schedule_map = _expand_date_map(calendar_cfg.get("afternoon_shutdown_schedule") or {})
            if schedule_map:
                overrides["afternoon_shutdown_schedule"] = schedule_map

    enable_morning = calendar_cfg.get("enable_morning_shutdown")
    if enable_morning is not False:
        morning_days = _expand_date_items(calendar_cfg.get("morning_shutdown_dates") or [])
        if morning_days or enable_morning is True:
            overrides["enable_morning_shutdown"] = bool(morning_days or enable_morning)
            if morning_days:
                overrides["morning_shutdown_dates"] = morning_days
            range_text = calendar_cfg.get("morning_shutdown", "08:00-12:00")
            parsed = _parse_time_range(range_text) or {}
            if parsed.get("start"):
                overrides["morning_shutdown_start"] = parsed["start"]
            if parsed.get("end"):
                overrides["morning_shutdown_end"] = parsed["end"]
            schedule_map = _expand_date_map(calendar_cfg.get("morning_shutdown_schedule") or {})
            if schedule_map:
                overrides["morning_shutdown_schedule"] = schedule_map

    if calendar_cfg.get("enable_lunch_break"):
        overrides["enable_lunch_break"] = True
        range_text = calendar_cfg.get("lunch_break", "12:00-13:00")
        parsed = _parse_time_range(range_text) or {}
        if parsed.get("start"):
            overrides["lunch_break_start"] = parsed["start"]
        if parsed.get("end"):
            overrides["lunch_break_end"] = parsed["end"]

    return overrides


def get_constraint_overrides(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return constraint-related overrides from runtime config."""
    config = config or get_runtime_config()
    overrides = (config or {}).get("constraints", {}) or {}
    if not isinstance(overrides, dict):
        return {}

    # [AGENT-ADD] 날짜별 용량 오버라이드 사용 여부 플래그 처리
    def _apply_enable_flag(flag_key: str, target_key: str) -> None:
        flag = overrides.get(flag_key)
        if flag is False:
            overrides[target_key] = {}

    _apply_enable_flag("enable_daily_block_cap_overrides", "daily_block_cap_overrides")
    _apply_enable_flag("enable_daily_seam_cap_overrides", "daily_seam_cap_overrides")
    _apply_enable_flag("enable_daily_seam_cap_scales", "daily_seam_cap_scales")

    return overrides


def get_audit_constraint_overrides(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return audit-only constraint overrides from runtime config."""
    config = config or get_runtime_config()
    overrides = (config or {}).get("audit_constraints", {}) or {}
    if not isinstance(overrides, dict):
        return {}
    return dict(overrides)
