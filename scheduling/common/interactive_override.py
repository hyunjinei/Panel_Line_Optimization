"""Helpers for interactive user overrides during assembly decoding."""

from __future__ import annotations

import copy
from typing import Dict, List, Optional, Sequence, Tuple

from enhanced_environment.models import BayType, ConstraintViolation
from enhanced_environment.common.utils_core import get_line_group_and_workshop_code


def resolve_forced_prefix_block(
    forced_prefix_block_ids: Optional[Sequence[Optional[int]]],
    forced_prefix_idx: int,
    selected_block_ids: Sequence[int],
) -> Tuple[Optional[int], int]:
    """Resolve the current step's forced block.

    `None` acts as a placeholder meaning "do not force at this step".
    """
    if not forced_prefix_block_ids:
        return None, forced_prefix_idx

    next_idx = forced_prefix_idx
    selected_set = set(selected_block_ids)
    while next_idx < len(forced_prefix_block_ids):
        raw_candidate = forced_prefix_block_ids[next_idx]
        next_idx += 1
        if raw_candidate is None:
            return None, next_idx
        candidate_id = int(raw_candidate)
        if candidate_id in selected_set:
            continue
        return candidate_id, next_idx
    return None, next_idx


# ==== [AGENT-ADD BEGIN: interactive precedence helpers] ====
def normalize_precedence_rules(
    precedence_rules: Optional[Sequence[Tuple[int, int]]],
) -> List[Tuple[int, int]]:
    """Return sanitized `(before, after)` precedence pairs."""
    normalized: List[Tuple[int, int]] = []
    for pair in precedence_rules or []:
        if not pair or len(pair) != 2:
            continue
        before_block_id, after_block_id = pair
        if before_block_id is None or after_block_id is None:
            continue
        before_block_id = int(before_block_id)
        after_block_id = int(after_block_id)
        if before_block_id == after_block_id:
            continue
        normalized.append((before_block_id, after_block_id))
    return normalized


def filter_available_ids_by_precedence(
    *,
    available_ids: Sequence[int],
    selected_block_ids: Sequence[int],
    precedence_rules: Optional[Sequence[Tuple[int, int]]],
) -> Tuple[List[int], List[int], Optional[int]]:
    """Filter candidates that violate interactive precedence constraints.

    Returns:
        filtered_available_ids,
        blocked_after_ids,
        fallback_before_block_id (used only when all candidates are blocked).
    """
    normalized_rules = normalize_precedence_rules(precedence_rules)
    if not normalized_rules:
        return list(available_ids), [], None

    selected_set = {int(block_id) for block_id in selected_block_ids}
    available_set = {int(block_id) for block_id in available_ids}
    blocked_after_ids: List[int] = []
    unresolved_before_ids: List[int] = []

    for before_block_id, after_block_id in normalized_rules:
        if before_block_id in selected_set:
            continue
        unresolved_before_ids.append(before_block_id)
        if after_block_id in available_set:
            blocked_after_ids.append(after_block_id)

    blocked_after_set = set(blocked_after_ids)
    filtered_available_ids = [int(block_id) for block_id in available_ids if int(block_id) not in blocked_after_set]

    fallback_before_block_id = None
    if not filtered_available_ids:
        for before_block_id in unresolved_before_ids:
            if before_block_id not in selected_set:
                fallback_before_block_id = int(before_block_id)
                break

    return filtered_available_ids, sorted(blocked_after_set), fallback_before_block_id


def normalize_manual_bay_assignments(
    manual_bay_assignments: Optional[Dict[int, str]],
) -> Dict[int, str]:
    """Return sanitized `{block_id: bay}` mapping for interactive overrides."""
    normalized: Dict[int, str] = {}
    for block_id, bay in (manual_bay_assignments or {}).items():
        if block_id is None or not bay:
            continue
        bay_name = str(bay).upper()
        if bay_name not in {BayType.BAY_35A.value, BayType.BAY_36B.value}:
            continue
        normalized[int(block_id)] = bay_name
    return normalized


def apply_manual_bay_override_if_requested(
    *,
    env,
    selected_block_id: int,
    manual_bay_assignments: Optional[Dict[int, str]],
    final_reason: str,
) -> Optional[str]:
    """Register a one-step manual bay override if requested for this block."""
    normalized = normalize_manual_bay_assignments(manual_bay_assignments)
    requested_bay = normalized.get(int(selected_block_id))
    if requested_bay is None:
        return None

    env._set_manual_bay_override(
        int(selected_block_id),
        BayType(requested_bay),
        {"final_reason": final_reason},
    )
    return requested_bay
# ==== [AGENT-ADD END] ====


def prime_forced_override_cache(
    *,
    env,
    forced_block_id: int,
    masking_violations: List[ConstraintViolation],
    block_analysis: List[Dict],
    blocks_dict: Dict[int, object],
    note: str,
) -> Dict:
    """Prime env.step cache so an unavailable block can still be stepped through.

    # [AGENT-ADD] This is used only for interactive user overrides.
    # The core scheduler remains unchanged unless the caller explicitly enables it.
    """

    forced_analysis = next(
        (copy.deepcopy(analysis) for analysis in block_analysis if analysis.get("block_id") == forced_block_id),
        None,
    )

    if forced_analysis is None:
        block = blocks_dict[forced_block_id]
        line_group, assembly_code = get_line_group_and_workshop_code(block)
        forced_analysis = {
            "block_id": forced_block_id,
            "assembly_type": block.assembly_type.value,
            "line_group": line_group,
            "assembly_workshop_code": assembly_code,
            "port_starboard": block.port_starboard.value,
            "is_available": False,
            "inclusion_reason": "",
            "constraint_checks": {},
        }

    forced_analysis["is_available"] = True
    forced_analysis["was_masked_out_before_override"] = True
    forced_analysis["inclusion_reason"] = f"[USER-FORCED] {note}"

    relevant_violations = [
        violation
        for violation in masking_violations
        if getattr(violation, "block_id", None) == forced_block_id
    ]

    env._assembly_last_available_ids = [forced_block_id]
    env._assembly_last_block_analysis = [forced_analysis]
    env._assembly_last_violations = relevant_violations
    return forced_analysis
