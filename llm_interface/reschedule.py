"""Interactive rescheduling helpers backed by the existing PBS schedulers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .schemas import ScheduleEditRequest


def _append_unique_block_id(target: List[Optional[int]], block_id: Optional[int]) -> None:
    """[AGENT-ADD] Preserve request order while avoiding duplicate forced steps."""
    if block_id is None:
        return
    normalized = int(block_id)
    if normalized in {int(value) for value in target if value is not None}:
        return
    target.append(normalized)


def _normalize_date_key(raw_key: Any) -> Optional[str]:
    if raw_key is None:
        return None
    if isinstance(raw_key, datetime):
        return raw_key.strftime("%Y%m%d")
    text = str(raw_key).strip()
    if not text:
        return None
    digits = text.replace("-", "").replace("/", "").replace(".", "")
    if digits.isdigit() and len(digits) == 8:
        return digits
    return None



def _resolve_date_token(date_token: Optional[str], *, start_date: Optional[str] = None) -> Optional[str]:
    if not date_token:
        return None
    normalized = _normalize_date_key(date_token)
    if normalized:
        return normalized

    text = str(date_token).strip().upper()
    if text.startswith("DAY:") and start_date:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        target_day = int(text.split(":", 1)[1])
        return start_dt.replace(day=target_day).strftime("%Y%m%d")
    return None



def build_forced_prefix_plan(request: ScheduleEditRequest) -> List[Optional[int]]:
    """Build a position-aware forced prefix plan.

    Example:
    - "4번 블록은 처음" -> [4]
    - "4번 블록은 세 번째" -> [None, None, 4]

    # [AGENT-ADD] v1 converts fixed-position requests into prefix-resume plans.
    # [AGENT-EDIT] freeze_prefix and priority_block now map onto the same
    # scheduler-side forced prefix path used by fixed_position.
    """

    fixed_constraints = [c for c in request.constraints if c.type == "fixed_position"]
    prefix_plan: List[Optional[int]] = []

    for constraint in request.constraints:
        if constraint.type == "freeze_prefix":
            block_ids = list(constraint.block_ids or [])
            if not block_ids and constraint.block_id is not None:
                block_ids = [int(constraint.block_id)]
            for block_id in block_ids:
                _append_unique_block_id(prefix_plan, block_id)

    for constraint in request.constraints:
        if constraint.type == "priority_block":
            _append_unique_block_id(prefix_plan, constraint.block_id)

    if not fixed_constraints:
        return prefix_plan

    max_position = max(c.position or 0 for c in fixed_constraints)
    plan: List[Optional[int]] = list(prefix_plan)
    if len(plan) <= max_position:
        plan.extend([None] * (max_position + 1 - len(plan)))
    for constraint in fixed_constraints:
        if constraint.position is None or constraint.block_id is None:
            continue
        plan[constraint.position] = constraint.block_id

    return plan


# ==== [AGENT-ADD BEGIN: interactive constraint builders] ====
def build_precedence_rules(request: ScheduleEditRequest) -> List[Tuple[int, int]]:
    """Build `(before, after)` precedence rules from the parsed request."""
    rules: List[Tuple[int, int]] = []
    priority_ids = [
        int(constraint.block_id)
        for constraint in request.constraints
        if constraint.type == "priority_block" and constraint.block_id is not None
    ]
    delayed_ids = [
        int(constraint.block_id)
        for constraint in request.constraints
        if constraint.type == "delayed_block" and constraint.block_id is not None
    ]
    for constraint in request.constraints:
        if constraint.type != "precedence":
            continue
        if constraint.before_block_id is None or constraint.after_block_id is None:
            continue
        before_block_id = int(constraint.before_block_id)
        after_block_id = int(constraint.after_block_id)
        if before_block_id == after_block_id:
            continue
        rules.append((before_block_id, after_block_id))
    # [AGENT-ADD] A delayed block is represented in the existing scheduler by
    # requiring the urgent block to be selected before the delayed one.
    for priority_id in priority_ids:
        for delayed_id in delayed_ids:
            if priority_id != delayed_id:
                rules.append((priority_id, delayed_id))
    return rules



def build_manual_bay_assignments(request: ScheduleEditRequest) -> Dict[int, str]:
    """Build `{block_id: bay}` manual bay overrides from the parsed request."""
    assignments: Dict[int, str] = {}
    for constraint in request.constraints:
        if constraint.type != "manual_bay_assignment":
            continue
        if constraint.block_id is None or not constraint.bay:
            continue
        assignments[int(constraint.block_id)] = str(constraint.bay).upper()
    return assignments



def build_runtime_override_config(
    request: ScheduleEditRequest,
    *,
    start_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Build runtime_config-compatible overrides from parsed interactive requests."""
    constraint_overrides: Dict[str, Any] = {}
    audit_constraint_overrides: Dict[str, Any] = {}

    daily_block_caps: Dict[str, int] = {}
    daily_seam_caps: Dict[str, int] = {}

    for constraint in request.constraints:
        if constraint.type == "constraint_toggle" and constraint.target and constraint.enabled is not None:
            if constraint.scope in {"runtime", "both"}:
                constraint_overrides[str(constraint.target)] = bool(constraint.enabled)
            if constraint.scope in {"audit", "both"}:
                audit_constraint_overrides[str(constraint.target)] = bool(constraint.enabled)
            continue

        if constraint.type == "bias_components":
            constraint_overrides["bias_components"] = list(constraint.components or [])
            continue

        if constraint.type == "date_specific_daily_block_cap" and constraint.max_blocks is not None:
            date_key = _resolve_date_token(constraint.date_key or constraint.date_token, start_date=start_date)
            if date_key:
                daily_block_caps[date_key] = int(constraint.max_blocks)
            continue

        if constraint.type == "date_specific_daily_seam_cap" and constraint.seam_limit is not None:
            date_key = _resolve_date_token(constraint.date_key or constraint.date_token, start_date=start_date)
            if date_key:
                daily_seam_caps[date_key] = int(constraint.seam_limit)
            continue

    if daily_block_caps:
        constraint_overrides["daily_block_cap_overrides"] = daily_block_caps
    if daily_seam_caps:
        constraint_overrides["daily_seam_cap_overrides"] = daily_seam_caps

    config: Dict[str, Any] = {}
    if constraint_overrides:
        config["constraints"] = constraint_overrides
    if audit_constraint_overrides:
        config["audit_constraints"] = audit_constraint_overrides
    return config
# ==== [AGENT-ADD END] ====
