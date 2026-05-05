"""Shared result builders for scheduling outputs."""

# [AGENT-ADD] Extracted from assembly scheduling for reuse (RL + heuristics).

from datetime import datetime
from typing import Dict

from enhanced_environment.common.utils_core import (
    summarize_violations,
    get_line_group_and_workshop_code,
)


def create_block_result(
    block,
    assigned_bay,
    sequence,
    bay_analysis,
    violations,
    date_str=None,
    start_time=None,
    end_time=None,
    makespan_minutes=None,
    makespan_hours=None,
    total_completion_time=None,
    date_start_time=None,
    actual_machine_2_start_time=None,
) -> Dict:
    """블록 결과 생성 (공통 포맷)."""
    # ==== [AGENT-EDIT BEGIN: canonical violation summary] ====
    # raw / primary / meta / info를 공통 유틸에서 동일 기준으로 산출한다.
    violation_summary = summarize_violations(
        violations,
        keep_info=True,
        include_guard=False,
    )
    violation_count = int(violation_summary.get("violations_primary_count", 0))
    # ==== [AGENT-EDIT END] ====

    line_group, assembly_code = get_line_group_and_workshop_code(block)

    plan_date_value = date_str or datetime.now().strftime('%Y%m%d')
    actual_date_value = plan_date_value
    if start_time:
        actual_date_value = start_time.strftime('%Y%m%d')

    result = {
        'date': actual_date_value,
        'am_sequence': sequence,
        'block_id': block.block_id,
        'block_name': getattr(block, 'block_name', f'BLK_{block.block_id}'),
        'port_starboard': block.port_starboard.value,
        'assembly_type': block.assembly_type.value,
        'line_group': line_group,
        'assembly_workshop_code': assembly_code,
        'block_assembly_date': block.assembly_start_date.strftime('%Y-%m-%d') if hasattr(block, 'assembly_start_date') and block.assembly_start_date else '',
        'width_m': round(block.width, 1),
        'longi_count': block.longi_count,
        'seam_count': block.seam_count,
        'c_seam_count': getattr(block, 'c_seam_count', 0),
        'has_curved_plate': getattr(block, 'has_curved_plate', False),
        'main_plate_count': block.main_plate_count,
        'original_bay': assigned_bay.value,
        'assigned_bay': assigned_bay.value,
        'bay_changed': False,
        'total_time_min': round(sum(block.processing_times), 1),
        'material_ready': block.material_ready,
        'is_fab': block.is_fab,
        'is_draft': block.is_draft,
        'is_cross_seam': block.is_cross_seam,
        'material_type': block.material_type.value,
        'is_subassembly': getattr(block, 'is_subassembly', False),
        'method': 'ASSEMBLY',
        'plan_date': plan_date_value,
        'excel_sequence': getattr(block, 'sequence_number', None),
        **violation_summary,
        # [AGENT-EDIT] 기존 필드 호환성 유지: violations는 primary count alias
        'violations': violation_count,
        # [AGENT-EDIT] 시간 필드를 분리하여 panel / machine2 / final 종료를 명확히 남긴다.
        'panel_start_time': start_time.strftime('%Y-%m-%d %H:%M') if start_time else '',
        'machine2_start_time': actual_machine_2_start_time.strftime('%Y-%m-%d %H:%M') if actual_machine_2_start_time else '',
        'final_end_time': end_time.strftime('%Y-%m-%d %H:%M') if end_time else '',
        # [AGENT-EDIT] primary metric의 start_time은 panel start alias로 고정
        'start_time': start_time.strftime('%Y-%m-%d %H:%M') if start_time else '',
        'end_time': end_time.strftime('%Y-%m-%d %H:%M') if end_time else '',
        'date_start_time': date_start_time.strftime('%Y-%m-%d %H:%M') if date_start_time else '',
        'date_completion_time': total_completion_time.strftime('%Y-%m-%d %H:%M') if total_completion_time else '',
        'makespan_minutes': round(makespan_minutes, 1) if makespan_minutes else 0,
        'makespan_hours': round(makespan_hours, 2) if makespan_hours else 0,
        'process_1_time_min': round(block.processing_times[0], 1) if len(block.processing_times) > 0 else 0,
        'process_2_time_min': round(block.processing_times[1], 1) if len(block.processing_times) > 1 else 0,
        'process_3_time_min': round(block.processing_times[2], 1) if len(block.processing_times) > 2 else 0,
        'process_4_time_min': round(block.processing_times[3], 1) if len(block.processing_times) > 3 else 0,
        'process_5_time_min': round(block.processing_times[4], 1) if len(block.processing_times) > 4 else 0,
        'process_6_time_min': round(block.processing_times[5], 1) if len(block.processing_times) > 5 else 0,
        'process_7_time_min': round(block.processing_times[6], 1) if len(block.processing_times) > 6 else 0,
        'process_8_time_min': round(block.processing_times[7], 1) if len(block.processing_times) > 7 else 0,
    }

    subassembly_expansion = []
    for original in getattr(block, 'subassembly_original_blocks', [block]):
        sub_line_group, sub_assembly_code = get_line_group_and_workshop_code(original)
        subassembly_expansion.append({
            'block_id': original.block_id,
            'block_name': getattr(original, 'block_name', f'BLK_{original.block_id}'),
            'port_starboard': original.port_starboard.value,
            'assembly_type': original.assembly_type.value,
            'line_group': sub_line_group,
            'assembly_workshop_code': sub_assembly_code,
            'block_assembly_date': original.assembly_start_date.strftime('%Y-%m-%d') if getattr(original, 'assembly_start_date', None) else '',
            'width_m': round(original.width, 1),
            'longi_count': original.longi_count,
            'seam_count': original.seam_count,
            'c_seam_count': getattr(original, 'c_seam_count', 0),
            'has_curved_plate': getattr(original, 'has_curved_plate', False),
            'main_plate_count': original.main_plate_count
        })
    result['subassembly_expansion'] = subassembly_expansion

    return result
