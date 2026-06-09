"""Validation helpers for structured LLM scheduling requests."""

# [AGENT-ADD] Keep LLM output bounded before it reaches the scheduler.

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .schemas import EditConstraint, ScheduleEditRequest


SUPPORTED_CONSTRAINT_TYPES = {
    "fixed_position",
    "freeze_prefix",
    "priority_block",
    "delayed_block",
    "precedence",
    "manual_bay_assignment",
    "constraint_toggle",
    "bias_components",
    "date_specific_daily_block_cap",
    "date_specific_daily_seam_cap",
}
SUPPORTED_BAYS = {"35A", "36B"}


def _to_int_list(raw: Any) -> List[int]:
    """[AGENT-ADD] Normalize list-like LLM fields before dataclass construction."""
    if raw is None:
        return []
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return [int(raw)]
    if isinstance(raw, str):
        values = [item.strip() for item in raw.replace(";", ",").split(",")]
    else:
        try:
            values = list(raw)
        except TypeError:
            return []

    normalized: List[int] = []
    for value in values:
        try:
            normalized.append(int(value))
        except Exception:
            continue
    return normalized


def request_to_dict(request: ScheduleEditRequest) -> Dict[str, Any]:
    """Return a JSON-serializable request payload."""
    return asdict(request)


def request_from_dict(payload: Dict[str, Any], raw_request: str = "") -> ScheduleEditRequest:
    """Build `ScheduleEditRequest` from deterministic or LLM-produced JSON."""
    constraints: List[EditConstraint] = []
    for raw_constraint in payload.get("constraints") or []:
        if not isinstance(raw_constraint, dict):
            continue
        allowed = EditConstraint.__dataclass_fields__.keys()
        kwargs = {key: value for key, value in raw_constraint.items() if key in allowed}
        # [AGENT-EDIT] Gemini/Groq may emit list fields as strings or numbers.
        for list_key in ("block_ids", "components"):
            if list_key in kwargs:
                kwargs[list_key] = _to_int_list(kwargs[list_key])
        constraints.append(EditConstraint(**kwargs))

    return ScheduleEditRequest(
        raw_request=str(payload.get("raw_request") or raw_request or ""),
        mode=str(payload.get("mode") or "strict"),
        preserve_existing_schedule_as_much_as_possible=bool(
            payload.get("preserve_existing_schedule_as_much_as_possible", False)
        ),
        constraints=constraints,
        unparsed_fragments=[str(item) for item in (payload.get("unparsed_fragments") or [])],
    )


def _normalise_sequence(sequence: Sequence[int] | None) -> List[int]:
    if not sequence:
        return []
    normalised: List[int] = []
    for value in sequence:
        try:
            normalised.append(int(value))
        except Exception:
            continue
    return normalised


def validate_request(
    request: ScheduleEditRequest,
    *,
    sequence: Sequence[int] | None = None,
) -> Tuple[List[str], List[str]]:
    """Validate a parsed edit request.

    Returns `(errors, warnings)`. Errors mean the request should not be run.
    Warnings mean it can run, but the user should see the limitation.
    """

    errors: List[str] = []
    warnings: List[str] = []
    sequence_ids = set(_normalise_sequence(sequence))
    sequence_length = len(sequence_ids)

    if request.mode not in {"strict", "repair"}:
        warnings.append(f"mode '{request.mode}'는 알 수 없어 strict처럼 처리합니다.")

    if not request.constraints:
        warnings.append("파싱된 제약이 없습니다.")

    fixed_positions: Dict[int, int] = {}
    manual_bays: Dict[int, str] = {}
    precedence_pairs: set[tuple[int, int]] = set()
    frozen_prefix_ids: List[int] = []
    priority_ids: List[int] = []
    delayed_ids: List[int] = []

    for idx, constraint in enumerate(request.constraints, 1):
        ctype = str(constraint.type or "").strip()
        if ctype not in SUPPORTED_CONSTRAINT_TYPES:
            errors.append(f"{idx}번째 제약 type '{ctype}'는 지원하지 않습니다.")
            continue

        if ctype == "fixed_position":
            if constraint.block_id is None or constraint.position is None:
                errors.append("fixed_position은 block_id와 position이 필요합니다.")
                continue
            block_id = int(constraint.block_id)
            position = int(constraint.position)
            if sequence_ids and block_id not in sequence_ids:
                errors.append(f"block_id {block_id}는 현재 시퀀스에 없습니다.")
            if position < 0:
                errors.append(f"block_id {block_id}의 position은 0 이상이어야 합니다.")
            if sequence_length and position >= sequence_length:
                errors.append(f"block_id {block_id}의 position {position}이 시퀀스 길이를 넘습니다.")
            if position in fixed_positions and fixed_positions[position] != block_id:
                errors.append(f"position {position}에 서로 다른 블록이 동시에 고정됐습니다.")
            fixed_positions[position] = block_id

        elif ctype == "freeze_prefix":
            block_ids = _to_int_list(constraint.block_ids or ([] if constraint.block_id is None else [constraint.block_id]))
            if not block_ids:
                errors.append("freeze_prefix는 block_ids가 필요합니다.")
                continue
            if len(block_ids) != len(set(block_ids)):
                errors.append("freeze_prefix 안에 같은 block_id가 중복됐습니다.")
            for block_id in block_ids:
                if sequence_ids and block_id not in sequence_ids:
                    errors.append(f"freeze_prefix block_id {block_id}가 현재 시퀀스에 없습니다.")
            frozen_prefix_ids.extend(block_ids)

        elif ctype == "priority_block":
            if constraint.block_id is None:
                errors.append("priority_block은 block_id가 필요합니다.")
                continue
            block_id = int(constraint.block_id)
            if sequence_ids and block_id not in sequence_ids:
                errors.append(f"priority_block block_id {block_id}가 현재 시퀀스에 없습니다.")
            priority_ids.append(block_id)

        elif ctype == "delayed_block":
            if constraint.block_id is None:
                errors.append("delayed_block은 block_id가 필요합니다.")
                continue
            block_id = int(constraint.block_id)
            if sequence_ids and block_id not in sequence_ids:
                errors.append(f"delayed_block block_id {block_id}가 현재 시퀀스에 없습니다.")
            delayed_ids.append(block_id)

        elif ctype == "precedence":
            if constraint.before_block_id is None or constraint.after_block_id is None:
                errors.append("precedence는 before_block_id와 after_block_id가 필요합니다.")
                continue
            before_id = int(constraint.before_block_id)
            after_id = int(constraint.after_block_id)
            if before_id == after_id:
                errors.append("precedence에서 before와 after가 같은 블록입니다.")
            if sequence_ids and before_id not in sequence_ids:
                errors.append(f"precedence before_block_id {before_id}가 현재 시퀀스에 없습니다.")
            if sequence_ids and after_id not in sequence_ids:
                errors.append(f"precedence after_block_id {after_id}가 현재 시퀀스에 없습니다.")
            if (after_id, before_id) in precedence_pairs:
                errors.append(f"precedence 순환 가능성이 있습니다: {before_id} <-> {after_id}")
            precedence_pairs.add((before_id, after_id))

        elif ctype == "manual_bay_assignment":
            if constraint.block_id is None or not constraint.bay:
                errors.append("manual_bay_assignment는 block_id와 bay가 필요합니다.")
                continue
            block_id = int(constraint.block_id)
            bay = str(constraint.bay).upper()
            if sequence_ids and block_id not in sequence_ids:
                errors.append(f"manual bay block_id {block_id}가 현재 시퀀스에 없습니다.")
            if bay not in SUPPORTED_BAYS:
                errors.append(f"bay '{bay}'는 지원하지 않습니다. 허용값: 35A, 36B")
            if block_id in manual_bays and manual_bays[block_id] != bay:
                errors.append(f"block_id {block_id}에 서로 다른 bay가 동시에 지정됐습니다.")
            manual_bays[block_id] = bay

        elif ctype == "constraint_toggle":
            if not constraint.target:
                errors.append("constraint_toggle은 target이 필요합니다.")
            if constraint.enabled is None:
                errors.append("constraint_toggle은 enabled가 필요합니다.")
            if constraint.scope not in {"runtime", "audit", "both"}:
                errors.append(f"constraint_toggle scope '{constraint.scope}'는 runtime/audit/both 중 하나여야 합니다.")

        elif ctype == "bias_components":
            invalid = [value for value in constraint.components if int(value) not in {1, 2, 3}]
            if invalid:
                errors.append(f"bias_components는 1, 2, 3만 허용합니다: {invalid}")

        elif ctype == "date_specific_daily_block_cap":
            if not (constraint.date_key or constraint.date_token):
                errors.append("date_specific_daily_block_cap은 날짜가 필요합니다.")
            if constraint.max_blocks is None or int(constraint.max_blocks) < 0:
                errors.append("date_specific_daily_block_cap의 max_blocks는 0 이상이어야 합니다.")

        elif ctype == "date_specific_daily_seam_cap":
            if not (constraint.date_key or constraint.date_token):
                errors.append("date_specific_daily_seam_cap은 날짜가 필요합니다.")
            if constraint.seam_limit is None or int(constraint.seam_limit) < 0:
                errors.append("date_specific_daily_seam_cap의 seam_limit은 0 이상이어야 합니다.")

    forced_like_ids = frozen_prefix_ids + priority_ids
    if len(forced_like_ids) != len(set(forced_like_ids)):
        errors.append("freeze_prefix와 priority_block 사이에 같은 block_id가 중복됐습니다.")
    for block_id in delayed_ids:
        if block_id in priority_ids:
            errors.append(f"block_id {block_id}는 priority_block과 delayed_block에 동시에 지정될 수 없습니다.")
        if block_id in frozen_prefix_ids:
            errors.append(f"block_id {block_id}는 freeze_prefix와 delayed_block에 동시에 지정될 수 없습니다.")
    if delayed_ids and not priority_ids:
        warnings.append("delayed_block은 priority_block과 함께 있을 때 즉시 투입 지연 효과가 명확합니다.")

    return errors, warnings


def collect_block_ids(rows: Iterable[Dict[str, Any]]) -> List[int]:
    """Extract an ordered block-id sequence from result rows."""
    ordered = sorted(rows, key=lambda row: int(row.get("am_sequence", 0) or 0))
    block_ids: List[int] = []
    for row in ordered:
        value = row.get("block_id")
        if value is None:
            continue
        try:
            block_ids.append(int(value))
        except Exception:
            continue
    return block_ids
