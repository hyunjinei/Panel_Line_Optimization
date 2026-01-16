"""Selection rules for scheduling candidates (SPT/LPT/SEAM/priority/random)."""

# [AGENT-ADD] Extracted to standardize selection behavior across schedulers.

import random
from typing import Dict, List, Tuple


def select_block_id(
    available_ids: List[int],
    blocks_dict: Dict,
    selection_method: str,
) -> Tuple[int, str]:
    """Select a block id and return (id, reason)."""
    if not available_ids:
        return None, "후보 없음"

    if selection_method == "priority":
        available_blocks_with_date = []
        for block_id in available_ids:
            block = blocks_dict[block_id]
            available_blocks_with_date.append((block_id, block.assembly_start_date))
        available_blocks_with_date.sort(key=lambda x: x[1])
        selected_block_id = available_blocks_with_date[0][0]
        selection_reason = (
            f"우선순위 선택 (조립착수일: {available_blocks_with_date[0][1].strftime('%Y-%m-%d')})"
        )
    elif selection_method == "random":
        selected_block_id = random.choice(available_ids)
        selection_reason = f"랜덤 선택 ({len(available_ids)}개 중)"
    elif selection_method == "spt":
        min_processing_time = float('inf')
        selected_block_id = available_ids[0]
        for block_id in available_ids:
            block = blocks_dict[block_id]
            total_processing_time = sum(block.processing_times)
            if total_processing_time < min_processing_time:
                min_processing_time = total_processing_time
                selected_block_id = block_id
        selection_reason = (
            f"SPT 선택 (처리시간: {min_processing_time:.1f}분, {len(available_ids)}개 중)"
        )
    elif selection_method == "lpt":
        max_processing_time = 0
        selected_block_id = available_ids[0]
        for block_id in available_ids:
            block = blocks_dict[block_id]
            total_processing_time = sum(block.processing_times)
            if total_processing_time > max_processing_time:
                max_processing_time = total_processing_time
                selected_block_id = block_id
        selection_reason = (
            f"LPT 선택 (처리시간: {max_processing_time:.1f}분, {len(available_ids)}개 중)"
        )
    elif selection_method == "seam_min":
        min_seam_count = float('inf')
        selected_block_id = available_ids[0]
        for block_id in available_ids:
            block = blocks_dict[block_id]
            seam_count = block.seam_count
            if seam_count < min_seam_count:
                min_seam_count = seam_count
                selected_block_id = block_id
        selection_reason = (
            f"SEAM_MIN 선택 (SEAM 수: {min_seam_count}개, {len(available_ids)}개 중)"
        )
    else:
        selected_block_id = available_ids[0]
        selection_reason = "기본 선택 (첫 번째)"

    return selected_block_id, selection_reason
