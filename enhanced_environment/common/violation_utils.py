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
    primary, info = dedup_violations(
        violations,
        keep_info=keep_info,
        include_guard=include_guard,
    )
    relaxed_constraints = extract_relax_constraints(violations)
    relax_event_count = count_relax_events(violations)

    return {
        "violations": len(primary),
        "violation_details": [v.message for v in primary],
        "violation_severity": [v.severity for v in primary],
        "constraint_ids": [v.constraint_id for v in primary],
        "info_count": len(info) if keep_info else 0,
        "info_details": [v.message for v in info] if keep_info else [],
        "info_constraint_ids": [v.constraint_id for v in info] if keep_info else [],
        "info_severity": [v.severity for v in info] if keep_info else [],
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
    # [AGENT-EDIT] 메타/완화 이벤트는 제약 위반 카운트에서 제외하고 info로 분리
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
    }

    primary: Dict[str, ConstraintViolation] = {}
    info_only: List[ConstraintViolation] = []

    for violation in violations or []:
        cid = getattr(violation, "constraint_id", "") or ""
        sev = (getattr(violation, "severity", "") or "INFO").upper()

        if cid in guard_ids and not include_guard:
            info_only.append(violation)
            continue

        if sev == "INFO" and keep_info:
            info_only.append(violation)
            continue

        # constraint_id 기준으로 최상 severity만 유지
        if cid not in primary:
            primary[cid] = violation
        else:
            current = primary[cid]
            current_sev = (getattr(current, "severity", "") or "INFO").upper()
            if _SEVERITY_PRIORITY.get(sev, 0) > _SEVERITY_PRIORITY.get(current_sev, 0):
                primary[cid] = violation

    primary_list = list(primary.values())
    primary_list.sort(
        key=lambda v: (
            -_SEVERITY_PRIORITY.get((v.severity or "INFO").upper(), 0),
            v.constraint_id or "",
        )
    )

    return primary_list, info_only


__all__ = [
    "_SEVERITY_PRIORITY",
    "RELAX_META_IDS",
    "RELAX_EVENT_IDS",
    "extract_relax_constraints",
    "count_relax_events",
    "summarize_violations",
    "dedup_violations",
]
