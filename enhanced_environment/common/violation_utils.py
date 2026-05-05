# [AGENT-ADD] ConstraintViolation helper utilities split from utils_core.py.

import re
from typing import List, Dict, Tuple, Optional, Any, Set

from enhanced_environment.models import ConstraintViolation

# ====================================================================================================
# [AGENT-ADD] ConstraintViolation helpers
# ====================================================================================================
_SEVERITY_PRIORITY = {"ERROR": 3, "WARNING": 2, "INFO": 1}

# [AGENT-ADD] 완화/메타 이벤트 식별용 ID (완화 횟수/목록 집계)
RELAX_META_IDS = {
    "RELAX_STAGE",
    "RELAX_STAGE_DETAIL",
    "RELAX_STAGE_FINAL",
    "URGENT_RELAX",
}

# [AGENT-ADD] "완화 이벤트" 카운트에 포함할 ID (DETAIL 제외)
RELAX_EVENT_IDS = {
    "RELAX_STAGE",
    "RELAX_STAGE_FINAL",
    "URGENT_RELAX",
}

# [AGENT-ADD] primary metric에서 분리할 메타/완화 prefix
META_ID_PREFIXES = (
    "EMERGENCY_",
    "RELAX_STAGE",
    "LEADTIME_GUARD",
    "PS_FORCED",
)

# [AGENT-ADD] normalized family 매핑
_FAMILY_MAP = {
    "P5#1": "DELIVERY_DATE_FAMILY",
    "P5#3": "PS_CONTINUITY_FAMILY",
    "P5#4": "PS_CONTINUITY_FAMILY",
    "P5#3,4": "PS_CONTINUITY_FAMILY",
    "P5#6": "FAB_INTERVAL_FAMILY",
    "P5#8": "CAPACITY_FAMILY",
    "P5#9": "CAPACITY_FAMILY",
    "P5#10": "CAPACITY_FAMILY",
    "P5#13": "MATERIAL_READY_FAMILY",
    "P5#15": "HOLIDAY_EVE_FAMILY",
    "P5#16": "CAPACITY_FAMILY",
    "P5#17": "SUBASSEMBLY_FAMILY",
    "P5#11": "ASSEMBLY_MIXING_FAMILY",
    "P5#12": "ASSEMBLY_MIXING_FAMILY",
    "P5#11,12": "ASSEMBLY_MIXING_FAMILY",
    "P6#1": "P6_TIME_FAMILY",
    "P6#2": "P6_TIME_FAMILY",
    "P6#3": "P6_TIME_FAMILY",
    "P6#1,2,3": "P6_TIME_FAMILY",
    "P6#1_2_3": "P6_TIME_FAMILY",
    "P6#4": "CROSS_SEAM_MIXING_FAMILY",
    "P6#4_DETAIL": "CROSS_SEAM_MIXING_FAMILY",
    "ROUTING_C_SEAM_SPACING": "C_SEAM_SPACING_FAMILY",
    "C_SEAM_SPACING": "C_SEAM_SPACING_FAMILY",
    "ROUTING_CURVED_SPACING": "CURVED_SPACING_FAMILY",
    "ROUTING_HIGH_SEAM_SPACING": "HIGH_SEAM_SPACING_FAMILY",
    "ROUTING_WORKSHOP_ORDER": "WORKSHOP_ORDER_FAMILY",
    "LINE_GROUP_CONSTRAINT": "LINE_GROUP_FAMILY",
    "CONSECUTIVE_3BAY": "CONSECUTIVE_3BAY_FAMILY",
    "CONSECUTIVE_B_BAY": "CONSECUTIVE_B_BAY_FAMILY",
    "HIGH_LONGI_SPLIT": "HIGH_LONGI_SPLIT_FAMILY",
    "P7#1": "LONGI_BALANCE_FAMILY",
    "P7#2": "WIDE_BLOCK_BAY_FAMILY",
    "P7#3": "PS_SAME_BAY_FAMILY",
    "P7#4": "PS_SAME_BAY_FAMILY",
    "P7#7": "BAY_STREAK_FAMILY",
    "P7#8": "MAIN_PLATE_STREAK_FAMILY",
    "P7#10": "LONGI30_BAY_PREF_FAMILY",
    "P7#11": "BLOCK10_BAY_PATTERN_FAMILY",
    "P7#12": "LT_STEEL_BAY_PREF_FAMILY",
}


def normalize_constraint_family(constraint_id: str) -> str:
    """[AGENT-ADD] constraint_id를 논문/지표용 family로 정규화."""
    cid = (constraint_id or "").strip()
    if not cid:
        return "UNKNOWN_FAMILY"
    if cid in _FAMILY_MAP:
        return _FAMILY_MAP[cid]
    if cid.endswith("_DETAIL"):
        base_cid = cid[:-7]
        if base_cid in _FAMILY_MAP:
            return _FAMILY_MAP[base_cid]
    if cid.startswith(META_ID_PREFIXES):
        return "META_RELAX_FAMILY"
    return cid


def _normalize_message(message: str) -> str:
    return re.sub(r"\s+", " ", (message or "").strip().lower())


def _is_meta_violation(
    violation: ConstraintViolation,
    *,
    include_guard: bool = False,
    guard_constraint_ids: Optional[Set[str]] = None,
) -> bool:
    """[AGENT-ADD] primary metric에서 제외할 메타/완화 이벤트 판정."""
    cid = getattr(violation, "constraint_id", "") or ""
    msg = getattr(violation, "message", "") or ""
    guard_ids = guard_constraint_ids or {
        "LEADTIME_GUARD_CONSTRAINT",
        "LEADTIME_GUARD_DETAIL",
        "LEADTIME_GUARD",
        "EMERGENCY_MODE",
        "EMERGENCY_RELEASE",
        "EMERGENCY_PS_ONLY",
        "PS_ONLY_EMERGENCY",
        "URGENT_RELAX",
        "URGENT_FORCE",
        "RELAX_STAGE",
        "RELAX_STAGE_DETAIL",
        "RELAX_STAGE_FINAL",
        "PS_FORCED",
        "PS_FORCED_BLOCKED",
        "PS_FORCED_MISSING",
    }
    if cid in guard_ids and not include_guard:
        return True
    if cid.startswith(META_ID_PREFIXES):
        return True
    if "[RELAX]" in msg or "완화" in msg:
        return True
    return False


def build_violation_event_key(violation: ConstraintViolation) -> Tuple[str, str, str]:
    """[AGENT-ADD] family + block_id + normalized_message 기준 event key."""
    family = normalize_constraint_family(getattr(violation, "constraint_id", "") or "")
    block_id = str(getattr(violation, "block_id", "") or "")
    message = _normalize_message(getattr(violation, "message", "") or "")
    return family, block_id, message


def split_violations(
    violations: List[ConstraintViolation],
    *,
    keep_info: bool = True,
    include_guard: bool = False,
    guard_constraint_ids: Optional[Set[str]] = None,
) -> Tuple[List[ConstraintViolation], List[ConstraintViolation], List[ConstraintViolation], List[ConstraintViolation]]:
    """[AGENT-ADD] raw / primary / meta / info 분리."""
    raw = list(violations or [])
    primary: Dict[Tuple[str, str, str], ConstraintViolation] = {}
    meta_only: List[ConstraintViolation] = []
    info_only: List[ConstraintViolation] = []

    for violation in raw:
        sev = (getattr(violation, "severity", "") or "INFO").upper()

        if _is_meta_violation(
            violation,
            include_guard=include_guard,
            guard_constraint_ids=guard_constraint_ids,
        ):
            meta_only.append(violation)
            continue

        if sev == "INFO" and keep_info:
            info_only.append(violation)
            continue

        event_key = build_violation_event_key(violation)
        if event_key not in primary:
            primary[event_key] = violation
            continue

        current = primary[event_key]
        current_sev = (getattr(current, "severity", "") or "INFO").upper()
        if _SEVERITY_PRIORITY.get(sev, 0) > _SEVERITY_PRIORITY.get(current_sev, 0):
            primary[event_key] = violation

    primary_list = list(primary.values())
    primary_list.sort(
        key=lambda v: (
            -_SEVERITY_PRIORITY.get((v.severity or "INFO").upper(), 0),
            normalize_constraint_family(v.constraint_id or ""),
            v.constraint_id or "",
        )
    )
    return raw, primary_list, meta_only, info_only


def extract_relax_constraints(violations: List[ConstraintViolation]) -> List[str]:
    """완화 단계에서 해제된 제약 ID를 메시지에서 추출"""
    relaxed: Set[str] = set()
    for violation in violations or []:
        cid = getattr(violation, "constraint_id", "") or ""
        msg = getattr(violation, "message", "") or ""
        if cid not in RELAX_META_IDS and "[RELAX]" not in msg and "완화" not in msg:
            continue

        # 메시지 형식: "...: A, B, C"
        tail = ""
        if ":" in msg:
            tail = msg.split(":", 1)[1]
        if not tail:
            continue

        for token in re.split(r",", tail):
            token = token.strip()
            if not token:
                continue
            # 설명 문구 제거
            if "완화" in token:
                continue
            relaxed.add(token)

    return sorted(relaxed)


def count_relax_events(violations: List[ConstraintViolation]) -> int:
    """완화 이벤트 횟수(RELAX_STAGE 등)를 집계"""
    events: Set[str] = set()
    for violation in violations or []:
        cid = getattr(violation, "constraint_id", "") or ""
        msg = getattr(violation, "message", "") or ""
        if cid in RELAX_EVENT_IDS:
            events.add(f"{cid}:{msg}")

    # 메타 이벤트가 없는 경우, 메시지 기반으로 보정
    if not events:
        for violation in violations or []:
            cid = getattr(violation, "constraint_id", "") or ""
            msg = getattr(violation, "message", "") or ""
            if cid in RELAX_META_IDS:
                continue
            if "[RELAX]" in msg or "완화" in msg:
                events.add(msg)

    return len(events)


def summarize_violations(
    violations: List[ConstraintViolation],
    *,
    keep_info: bool = True,
    include_guard: bool = False
) -> Dict[str, Any]:
    """CSV 저장용 제약 요약(카운트/상세/완화) 생성"""
    raw, primary, meta, info = split_violations(
        violations,
        keep_info=keep_info,
        include_guard=include_guard,
    )
    relaxed_constraints = extract_relax_constraints(violations)
    relax_event_count = count_relax_events(violations)
    combined_info = meta + info
    primary_families = [normalize_constraint_family(v.constraint_id or "") for v in primary]
    meta_families = [normalize_constraint_family(v.constraint_id or "") for v in meta]
    info_families = [normalize_constraint_family(v.constraint_id or "") for v in info]

    return {
        "violations": len(primary),
        "violations_raw_count": len(raw),
        "violations_primary_count": len(primary),
        "violations_meta_count": len(meta),
        "violations_info_count": len(info),
        "violation_details": [v.message for v in primary],
        "violation_severity": [v.severity for v in primary],
        "constraint_ids": [v.constraint_id for v in primary],
        "constraint_families": primary_families,
        "raw_constraint_ids": [v.constraint_id for v in raw],
        "raw_constraint_families": [normalize_constraint_family(v.constraint_id or "") for v in raw],
        "meta_constraint_ids": [v.constraint_id for v in meta],
        "meta_constraint_families": meta_families,
        "meta_details": [v.message for v in meta],
        "meta_severity": [v.severity for v in meta],
        "info_count": len(combined_info) if keep_info else 0,
        "info_details": [v.message for v in combined_info] if keep_info else [],
        "info_constraint_ids": [v.constraint_id for v in combined_info] if keep_info else [],
        "info_constraint_families": meta_families + info_families if keep_info else [],
        "info_severity": [v.severity for v in combined_info] if keep_info else [],
        "relax_constraint_count": len(relaxed_constraints),
        "relax_constraint_ids": relaxed_constraints,
        "relax_event_count": relax_event_count,
    }


def dedup_violations(
    violations: List[ConstraintViolation],
    *,
    keep_info: bool = True,
    include_guard: bool = False,
    guard_constraint_ids: Optional[Set[str]] = None
) -> Tuple[List[ConstraintViolation], List[ConstraintViolation]]:
    """
    제약 위반 목록을 constraint_id 기준으로 중복 제거하고 대표 위반만 남깁니다.

    Args:
        violations: 원본 위반 리스트
        keep_info: INFO는 카운트에서 제외하고 info_details로 분리할지 여부
        include_guard: LEADTIME_GUARD_CONSTRAINT 같은 메타 경고를 주 카운트에 포함할지 여부
        guard_constraint_ids: 메타로 취급할 constraint_id 집합 (None이면 기본값 사용)

    Returns:
        primary: ERROR/WARNING 대표 리스트 (constraint_id별 1건, 최상위 severity)
        info_only: INFO 및 메타 경고 리스트 (CSV info_details용)
    """
    raw, primary, meta_only, info_only = split_violations(
        violations,
        keep_info=keep_info,
        include_guard=include_guard,
        guard_constraint_ids=guard_constraint_ids,
    )
    _ = raw
    return primary, meta_only + info_only


__all__ = [
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
]
