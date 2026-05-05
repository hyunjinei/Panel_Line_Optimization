"""Sequence edit helpers for structured user requests."""

from __future__ import annotations

from typing import List, Sequence

from .schemas import EditConstraint, ScheduleEditRequest


def _move_block(sequence: List[int], block_id: int, position: int) -> List[int]:
    if block_id not in sequence:
        raise ValueError(f"block_id {block_id} is not in the current sequence")
    edited = [x for x in sequence if x != block_id]
    clamped_position = max(0, min(position, len(edited)))
    edited.insert(clamped_position, block_id)
    return edited


def _enforce_precedence(sequence: List[int], before_block_id: int, after_block_id: int) -> List[int]:
    if before_block_id not in sequence or after_block_id not in sequence:
        raise ValueError(
            f"precedence blocks must both exist in the current sequence: "
            f"{before_block_id}, {after_block_id}"
        )
    edited = list(sequence)
    before_idx = edited.index(before_block_id)
    after_idx = edited.index(after_block_id)
    if before_idx < after_idx:
        return edited

    edited.pop(before_idx)
    after_idx = edited.index(after_block_id)
    edited.insert(after_idx, before_block_id)
    return edited


def apply_edit_request(sequence: Sequence[int], request: ScheduleEditRequest) -> List[int]:
    """Apply structured constraints to a block-id sequence.

    # [AGENT-ADD] v1 intentionally edits only the sequence.
    # Manual bay assignment is kept in the request schema for future scheduler-side usage.
    """

    edited = list(sequence)
    for constraint in request.constraints:
        edited = _apply_constraint(edited, constraint)
    return edited


def _apply_constraint(sequence: List[int], constraint: EditConstraint) -> List[int]:
    if constraint.type == "fixed_position":
        if constraint.block_id is None or constraint.position is None:
            raise ValueError("fixed_position requires block_id and position")
        return _move_block(sequence, constraint.block_id, constraint.position)

    if constraint.type == "precedence":
        if constraint.before_block_id is None or constraint.after_block_id is None:
            raise ValueError("precedence requires before_block_id and after_block_id")
        return _enforce_precedence(sequence, constraint.before_block_id, constraint.after_block_id)

    if constraint.type == "manual_bay_assignment":
        return list(sequence)

    raise ValueError(f"unsupported constraint type: {constraint.type}")
