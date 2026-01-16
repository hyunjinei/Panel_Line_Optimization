"""Shared result builders for scheduling outputs."""

# [AGENT-ADD] Extracted from assembly scheduling for reuse (RL + heuristics).

from datetime import datetime
from typing import Dict

from enhanced_environment.common.utils_core import (
    dedup_violations,
    extract_relax_constraints,
    count_relax_events,
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
    # [AGENT-ADD] 완화 제약/이벤트 추출 (중복 제거)
    relaxed_constraints = extract_relax_constraints(violations)
    relax_event_count = count_relax_events(violations)
    # [AGENT-ADD] 제약 중복 제거 및 INFO 분리 (카운트 안정화)
    primary_violations, info_violations = dedup_violations(
        violations,
        keep_info=True,
        include_guard=False
    )
    violation_count = len([v for v in primary_violations if v.severity in ['ERROR', 'WARNING']])

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
        'violations': violation_count,
        'violation_details': [v.message for v in primary_violations],
        'violation_severity': [v.severity for v in primary_violations],
        'constraint_ids': [v.constraint_id for v in primary_violations],
        'relax_constraint_count': len(relaxed_constraints),
        'relax_constraint_ids': relaxed_constraints,
        'relax_event_count': relax_event_count,
        'info_count': len(info_violations),
        'info_details': [v.message for v in info_violations],
        'info_constraint_ids': [v.constraint_id for v in info_violations],
        'info_severity': [v.severity for v in info_violations],
        'start_time': actual_machine_2_start_time.strftime('%Y-%m-%d %H:%M') if actual_machine_2_start_time else (start_time.strftime('%Y-%m-%d %H:%M') if start_time else ''),
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
